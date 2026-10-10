import os
import re
import json
import hmac
import uuid
import time
import shutil
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
import streamlit as st
from cryptography.fernet import Fernet, InvalidToken

APP_NAME = "ONE CLICK — ONE MOVIE RECAP"
BASE = Path(os.getenv("APP_DATA_DIR", "data")).resolve()
UPLOADS = BASE / "uploads"
PROJECTS = BASE / "projects"
for p in (BASE, UPLOADS, PROJECTS):
    p.mkdir(parents=True, exist_ok=True)
DB = BASE / "recap.db"

st.set_page_config(page_title=APP_NAME, page_icon="🎬", layout="wide", initial_sidebar_state="expanded")
ACCESS_PASSWORD = os.getenv("APP_ACCESS_PASSWORD", "").strip()
if not ACCESS_PASSWORD:
    st.error("App access is not configured. Set APP_ACCESS_PASSWORD in the hosting environment before using this app.")
    st.stop()
if not st.session_state.get("access_granted", False):
    st.markdown("### 🔒 Private workspace")
    candidate=st.text_input("App password",type="password")
    if st.button("Unlock app",type="primary",use_container_width=True):
        if hmac.compare_digest(candidate,ACCESS_PASSWORD):
            st.session_state["access_granted"]=True
            st.rerun()
        else:
            st.error("Password မမှန်ပါ။ ထပ်စစ်ပါ။")
    st.stop()

st.markdown("""
<style>
:root { color-scheme: dark; }
.stApp { background: radial-gradient(ellipse at top right,#20203a 0,#101117 42%,#0b0c10 100%); }
.block-container {max-width: 1450px; padding-top: 1.2rem; padding-bottom: 3rem}
.hero {padding:1.6rem 1.7rem;border:1px solid #393852;border-radius:20px;background:linear-gradient(120deg,#191927,#27213e 65%,#172438);margin-bottom:1.2rem}
.hero h1 {font-size:clamp(1.6rem,4vw,2.6rem);letter-spacing:-.04em;margin:0}
.hero p {color:#c4c5d7;margin:.5rem 0 0}
div[data-testid="stMetric"] {background:#171821;border:1px solid #30313e;padding:12px;border-radius:14px}
.stButton>button,.stDownloadButton>button {border-radius:11px;font-weight:650;min-height:2.7rem}
div[data-testid="stFileUploader"] {border:1px dashed #55516f;border-radius:14px;padding:8px}
section[data-testid="stSidebar"] {background:#11121a;border-right:1px solid #292a38}
</style>
<div class="hero"><h1>🎬 ONE CLICK — ONE MOVIE RECAP</h1><p>Upload a movie · Create a Burmese recap · Preview, edit and export</p></div>
""", unsafe_allow_html=True)

def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS projects(
        id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
        status TEXT NOT NULL, data TEXT NOT NULL, error TEXT DEFAULT '')""")
    con.execute("CREATE TABLE IF NOT EXISTS app_settings (name TEXT PRIMARY KEY, value BLOB NOT NULL)")
    con.commit()
    return con

def save_project(pid, title, status, data, error=""):
    with db() as con:
        con.execute("INSERT INTO projects(id,title,created_at,status,data,error) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,data=excluded.data,error=excluded.error",
                    (pid,title,datetime.utcnow().isoformat(timespec="seconds")+"Z",status,json.dumps(data,ensure_ascii=False),error))
        con.commit()

def encryption_box():
    key=os.getenv("APP_ENCRYPTION_KEY","").strip()
    if not key: return None
    try: return Fernet(key.encode("utf-8"))
    except Exception: return None

def load_saved_keys():
    box=encryption_box()
    if box is None: return {}
    values={}
    try:
        with db() as con:
            rows=con.execute("SELECT name,value FROM app_settings WHERE name IN ('groq_key','gemini_key')").fetchall()
        for row in rows:
            try: values[row["name"]]=box.decrypt(bytes(row["value"])).decode("utf-8")
            except (InvalidToken,ValueError): pass
    except Exception: pass
    return values

def save_encrypted_keys(groq_key,gemini_key):
    box=encryption_box()
    if box is None:
        raise RuntimeError("Persistent encrypted key saving requires a valid APP_ENCRYPTION_KEY environment variable.")
    with db() as con:
        for name,value in (("groq_key",groq_key),("gemini_key",gemini_key)):
            if value:
                con.execute("INSERT INTO app_settings(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                            (name,box.encrypt(value.encode("utf-8"))))
            else:
                con.execute("DELETE FROM app_settings WHERE name=?",(name,))
        con.commit()

def load_projects():
    with db() as con:
        return con.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()

def load_project(pid):
    with db() as con:
        row=con.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()
    return row

def run_cmd(args, timeout=3600):
    proc=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr or "Command failed")[-2200:])
    return proc.stdout.strip()

def ffmpeg_ok():
    return shutil.which("ffmpeg") and shutil.which("ffprobe")

def probe_video(path):
    raw=run_cmd(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],timeout=90)
    meta=json.loads(raw)
    video=next((x for x in meta.get("streams",[]) if x.get("codec_type")=="video"),None)
    audio=next((x for x in meta.get("streams",[]) if x.get("codec_type")=="audio"),None)
    if not video: raise ValueError("ဒီဖိုင်ထဲမှာ ဖတ်လို့ရတဲ့ video stream မတွေ့ပါ။")
    fmt=meta.get("format",{})
    return {"duration":float(fmt.get("duration",0) or 0),"size":int(fmt.get("size",0) or 0),
            "width":video.get("width"),"height":video.get("height"),
            "fps":video.get("r_frame_rate","unknown"),"has_audio":bool(audio)}

def srt_stamp(sec):
    ms=max(0,int(sec*1000)); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def split_subtitle_text(text, chars=25, max_lines=2):
    # Preserve Burmese codepoints; prefer whitespace and sentence boundaries.
    words=re.findall(r'\S+',text.strip())
    lines=[]; current=""
    for word in words:
        if not current: current=word
        elif len(current)+1+len(word)<=chars: current += " "+word
        else:
            lines.append(current); current=word
    if current: lines.append(current)
    segments=[]
    for i in range(0,len(lines),max_lines):
        segments.append("\n".join(lines[i:i+max_lines]))
    return segments or [text.strip()]

def make_srt(text,duration,chars=25,max_lines=2):
    parts=[]
    # Split by sentence first; split long sentences into subtitle-sized chunks.
    sentences=[s.strip() for s in re.split(r"(?<=[။!?])\s+|\n+",text) if s.strip()]
    if not sentences and text.strip(): sentences=[text.strip()]
    chunks=[]
    for sentence in sentences:
        chunks.extend(split_subtitle_text(sentence,chars,max_lines))
    if not chunks: return ""
    weights=[max(1,len(re.sub(r"\s","",x))) for x in chunks]
    total=sum(weights); cursor=0.0
    for i,(line,w) in enumerate(zip(chunks,weights),1):
        start=cursor; cursor += max(.4,(duration*w/total) if duration else 2.5)
        end=cursor
        parts.extend([str(i),f"{srt_stamp(start)} --> {srt_stamp(end)}",line,""])
    return "\n".join(parts)

def validate_srt(content):
    blocks=re.split(r"\n\s*\n",content.strip())
    good=[]
    pattern=re.compile(r"^(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,.]\d{3})$")
    for block in blocks:
        lines=block.replace("\r","").splitlines()
        if len(lines)<3 or not lines[0].strip().isdigit() or not pattern.match(lines[1].strip()):
            raise ValueError("SRT format မမှန်ပါ။ 1 / timestamp --> timestamp / subtitle text ပုံစံကို စစ်ပါ။")
        good.append("\n".join(lines))
    if not good: raise ValueError("SRT ထဲမှာ subtitle မရှိပါ။")
    return "\n\n".join(good)+"\n"

def transcribe(audio_path, api_key, model):
    from groq import Groq
    client=Groq(api_key=api_key, timeout=300, max_retries=2)
    with open(audio_path,"rb") as f:
        result=client.audio.transcriptions.create(file=(audio_path.name,f.read()),model=model,response_format="verbose_json",timestamp_granularities=["segment"])
    text=getattr(result,"text","") or ""
    segments=[]
    for s in (getattr(result,"segments",None) or []):
        segments.append({"start":float(getattr(s,"start",0)),"end":float(getattr(s,"end",0)),"text":getattr(s,"text","")})
    if not text.strip(): raise RuntimeError("Whisper က transcript အလွတ်ပြန်ပေးပါတယ်။")
    return text,segments

def gemini_models(api_key):
    from google import genai
    client=genai.Client(api_key=api_key)
    names=[]
    try:
        for item in client.models.list():
            name=getattr(item,"name","") or ""
            name=name.replace("models/","")
            if "gemini" in name.lower() and "tts" not in name.lower():
                names.append(name)
    except Exception:
        pass
    return sorted(set(names)),client

def generate_recap(transcript,api_key,requested_model,style):
    models,client=gemini_models(api_key)
    if requested_model in models: model=requested_model
    else:
        preferred=[m for m in models if "flash" in m.lower() and "preview" not in m.lower()]
        if not preferred: raise RuntimeError("Gemini API မှာ အသုံးပြုနိုင်တဲ့ text model မတွေ့ပါ။ Settings မှာ API key နဲ့ model access ကို စစ်ပါ။")
        model=preferred[0]
    # Chunk transcript to avoid sending an enormous single request.
    chunks=[transcript[i:i+18000] for i in range(0,len(transcript),18000)]
    notes=[]
    for idx,chunk in enumerate(chunks,1):
        prompt=("You are a careful movie-recap writer. Extract chronological plot facts, characters, motivations, key reveals, climax and ending. "
                "Do not invent facts. Return concise notes in Burmese. This is chunk "+str(idx)+" of "+str(len(chunks))+".\n\n"+chunk)
        resp=client.models.generate_content(model=model,contents=prompt)
        notes.append(resp.text or "")
    length_instruction={"တို":"တိုတောင်းသော recap","အလယ်အလတ်":"အလယ်အလတ်အရှည် recap","ရှည်":"အသေးစိတ်ရှည်လျားသော recap"}.get(style,"အလယ်အလတ်အရှည် recap")
    final_prompt=("အောက်ပါ plot notes ကို အခြေခံ၍ မြန်မာဘာသာဖြင့် သဘာဝကျပြီး နားထောင်ကောင်းသော movie recap narration ရေးပါ။ "
                  "အစ၊ အလယ်၊ အထွတ်အထိပ်၊ အဆုံးကို အစဉ်လိုက်ထိန်းပါ။ မူရင်းတွင်မရှိသောအဖြစ်အပျက် မတီထွင်ပါနှင့်။ "
                  "ထပ်နေသောအကြောင်းအရာဖယ်ပါ။ "+length_instruction+" ဖြစ်ရမည်။ ခေါင်းစဉ်/မှတ်ချက်မထည့်ဘဲ ဖတ်ပြမည့်စာသားသာ ပြန်ပါ။\n\n"+
                  "\n\n".join(notes)[:70000])
    resp=client.models.generate_content(model=model,contents=final_prompt)
    text=(resp.text or "").strip()
    if not text: raise RuntimeError("Gemini က recap စာသားအလွတ် ပြန်ပေးပါတယ်။")
    return text,model

def create_voice(text,output,voice,speed):
    import asyncio
    import edge_tts
    async def _go():
        rate=f"{int(speed):+d}%"
        await edge_tts.Communicate(text,voice,rate=rate).save(str(output))
    asyncio.run(_go())
    if not output.exists() or output.stat().st_size<1000:
        raise RuntimeError("TTS က audio ဖိုင် မထုတ်ပေးနိုင်ပါ။ Voice/provider ကို ပြောင်းစမ်းပါ။")
    return output

def esc_filter(path):
    return str(path).replace("\\\\","/").replace(":","\\:").replace("'","\\'").replace(",","\\,")

def render_video(video,voice,output,subtitle=None,settings=None,preview=False):
    settings=settings or {}
    meta=probe_video(video)
    cmd=["ffmpeg","-y"]
    if preview: cmd += ["-ss","0","-t","8"]
    cmd += ["-i",str(video),"-i",str(voice)]
    logo_path=settings.get("logo_path") if settings.get("logo_enabled") else None
    has_logo=bool(logo_path and Path(logo_path).exists())
    if has_logo: cmd += ["-i",str(logo_path)]
    graph=[]
    current="[0:v]"
    label_index=1
    if settings.get("blur_enabled"):
        x=max(0,int(settings.get("blur_x",20))); y=max(0,int(settings.get("blur_y",20)))
        w=max(2,int(settings.get("blur_w",200))); h=max(2,int(settings.get("blur_h",80)))
        strength=max(1,min(30,int(settings.get("blur_strength",8))))
        graph.append(f"[0:v]split=2[blurbase][blursrc];[blursrc]crop={w}:{h}:{x}:{y},boxblur={strength}:2[blurpatch];[blurbase][blurpatch]overlay={x}:{y}[v1]")
        current="[v1]"; label_index=2
    if settings.get("text_enabled") and settings.get("overlay_text","").strip():
        txt=esc_filter(settings["overlay_text"])
        fs=max(8,min(120,int(settings.get("text_size",32))))
        x=max(0,int(settings.get("text_x",20))); y=max(0,int(settings.get("text_y",20)))
        color=settings.get("text_color","#FFFFFF").replace("#","0x")
        out=f"[v{label_index}]"
        graph.append(f"{current}drawtext=text='{txt}':fontsize={fs}:fontcolor={color}:borderw=2:bordercolor=black:x={x}:y={y}{out}")
        current=out; label_index+=1
    if subtitle and settings.get("subtitles_enabled",True) and Path(subtitle).exists():
        style=f"FontSize={int(settings.get('subtitle_size',22))},Outline=2,Shadow=0,Alignment=2,MarginV=30"
        out=f"[v{label_index}]"
        graph.append(f"{current}subtitles='{esc_filter(subtitle)}':force_style='{style}'{out}")
        current=out; label_index+=1
    if has_logo:
        x=max(0,int(settings.get("logo_x",20))); y=max(0,int(settings.get("logo_y",20)))
        out=f"[v{label_index}]"
        size=max(5,min(100,int(settings.get("logo_size",20))))
        opacity=max(0.1,min(1.0,float(settings.get("logo_opacity",1.0))))
        logo_label=f"[logo{label_index}]"
        graph.append(f"[2:v]scale=iw*{size/100:.3f}:-1,format=rgba,colorchannelmixer=aa={opacity:.2f}{logo_label}")
        graph.append(f"{current}{logo_label}overlay={x}:{y}:format=auto{out}")
        current=out
    video_graph_present=bool(graph)
    audio_mode=settings.get("audio_mode","Narration only")
    if audio_mode=="Mix narration + original" and meta.get("has_audio"):
        graph.append("[0:a:0]volume=0.25[orig];[1:a:0]volume=1.0[narr];[orig][narr]amix=inputs=2:duration=shortest:dropout_transition=2[aout]")
        audio_map="[aout]"
    elif audio_mode=="Original audio only" and meta.get("has_audio"):
        audio_map="0:a:0"
    else:
        audio_map="1:a:0"
    if graph:
        cmd += ["-filter_complex",";".join(graph),"-map",current if video_graph_present else "0:v:0","-map",audio_map]
    else:
        cmd += ["-map","0:v:0","-map",audio_map]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf",str(settings.get("crf",23)),
            "-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(output)]
    run_cmd(cmd,timeout=7200)
    if not output.exists() or output.stat().st_size<10000: raise RuntimeError("FFmpeg output validation failed.")
    probe_video(output)
    return output

def project_dir(pid):
    p=PROJECTS/pid; p.mkdir(parents=True,exist_ok=True); return p

def put_download(label,path,name,mime):
    path=Path(path)
    if path.exists():
        st.download_button(label,path.read_bytes(),file_name=name,mime=mime,use_container_width=True)

with st.sidebar:
    st.markdown("### Navigation")
    page=st.radio("Go to",["Dashboard","Create Recap","Subtitle Studio","Voice Studio","Video Editor","Export History","Settings"],label_visibility="collapsed")
    st.divider()
    st.caption("Provider status")
    st.write("Whisper: Groq API")
    st.write("Recap: Gemini API")
    st.write("Voice: Edge TTS (my-MM)")
    st.caption("Projects are stored on this server's data directory. Hosted disk persistence depends on deployment settings.")

if "active_project" not in st.session_state: st.session_state.active_project=""
saved_keys=load_saved_keys()
if "api_groq" not in st.session_state: st.session_state.api_groq=os.getenv("GROQ_API_KEY","") or saved_keys.get("groq_key","")
if "api_gemini" not in st.session_state: st.session_state.api_gemini=os.getenv("GEMINI_API_KEY","") or saved_keys.get("gemini_key","")
if "api_gemini_model" not in st.session_state: st.session_state.api_gemini_model=os.getenv("GEMINI_MODEL","gemini-3.8-flash")

projects=load_projects()
if page=="Dashboard":
    rows=projects
    a,b,c=st.columns(3)
    a.metric("Projects",len(rows))
    b.metric("Completed",sum(r["status"]=="completed" for r in rows))
    c.metric("Processing / draft",sum(r["status"] not in ("completed","failed") for r in rows))
    st.markdown("### Recent projects")
    if not rows: st.info("Project မရှိသေးပါ။ Create Recap မှာ movie တင်ပြီး စတင်ပါ။")
    for r in rows[:8]:
        data=json.loads(r["data"])
        with st.container(border=True):
            x,y,z=st.columns([5,2,2])
            x.markdown("**"+r["title"]+"**")
            y.write(r["status"].upper())
            if z.button("Open",key="open_"+r["id"]):
                st.session_state.active_project=r["id"]; st.session_state["selected_page"]="Video Editor"; st.rerun()
            st.caption(f"Created {r['created_at']} · {r['id'][:8]}")
            if r["error"]: st.error(r["error"][:300])

elif page=="Create Recap":
    st.markdown("### Create a new movie recap")
    uploaded=st.file_uploader("Upload movie / video",type=["mp4","mov","mkv","webm","avi","m4v"],key="movie_upload")
    style=st.select_slider("Recap length",options=["တို","အလယ်အလတ်","ရှည်"],value="အလယ်အလတ်")
    groq_model=st.selectbox("Whisper model",["whisper-large-v3-turbo","whisper-large-v3"])
    model=st.text_input("Gemini model (auto-detect if unavailable)",value=st.session_state.api_gemini_model)
    st.caption("Keys ကို Settings မှာထည့်ပါ။ Video processing က server CPU/RAM/disk လိုအပ်ပါတယ်။")
    if uploaded:
        st.video(uploaded)
        st.write(f"File: {uploaded.name} · {uploaded.size/1024/1024:.1f} MB")
    if st.button("🚀 ONE CLICK RECAP",type="primary",use_container_width=True,disabled=not bool(uploaded)):
        if not st.session_state.api_groq or not st.session_state.api_gemini:
            st.error("Settings မှာ Groq နဲ့ Gemini API Key ထည့်ပါ။")
        elif not ffmpeg_ok():
            st.error("FFmpeg/FFprobe မတွေ့ပါ။ Deployment မှာ FFmpeg install လုပ်ထားရပါမယ်။")
        else:
            pid=uuid.uuid4().hex[:12]; root=project_dir(pid)
            original=root/("movie"+Path(uploaded.name).suffix.lower())
            original.write_bytes(uploaded.getvalue())
            data={"video":str(original),"assets":{},"settings":{"subtitles_enabled":True,"text_enabled":False,"blur_enabled":False,"logo_enabled":False},"style":style}
            save_project(pid,uploaded.name,"processing",data)
            st.session_state.active_project=pid
            try:
                with st.status("Processing movie…",expanded=True) as status:
                    status.write("1/5 · Video metadata စစ်နေသည်")
                    meta=probe_video(original); data["meta"]=meta
                    audio=root/"source_audio.mp3"
                    if not meta["has_audio"]: raise RuntimeError("ဒီ video မှာ audio stream မရှိပါ။ Voice file ထည့်ပြီး editor ကနေ render လုပ်နိုင်ပါတယ်။")
                    status.write("2/5 · FFmpeg ဖြင့် audio ထုတ်နေသည်")
                    run_cmd(["ffmpeg","-y","-i",str(original),"-vn","-ac","1","-ar","16000","-b:a","64k",str(audio)],timeout=900)
                    data["assets"]["source_audio"]=str(audio)
                    status.write("3/5 · Groq Whisper transcription")
                    transcript,segments=transcribe(audio,st.session_state.api_groq,groq_model)
                    tpath=root/"original_transcript.txt"; tpath.write_text(transcript,encoding="utf-8")
                    data["assets"]["transcript"]=str(tpath); data["transcript_segments"]=segments
                    status.write("4/5 · Gemini recap generation")
                    recap,actual_model=generate_recap(transcript,st.session_state.api_gemini,model,style)
                    rpath=root/"burmese_recap.txt"; rpath.write_text(recap,encoding="utf-8")
                    data["assets"]["recap"]=str(rpath); data["gemini_model_used"]=actual_model
                    status.write("5/5 · Subtitle draft")
                    srtpath=root/"burmese_recap.srt"
                    srtpath.write_text(make_srt(recap,meta["duration"]),encoding="utf-8")
                    data["assets"]["srt"]=str(srtpath)
                    status.write("6/6 · Burmese narration generation")
                    try:
                        voicepath=root/"narration.mp3"
                        create_voice(recap,voicepath,"my-MM-NilarNeural",0)
                        data["assets"]["voice"]=str(voicepath)
                        data["voice_provider"]="edge-tts my-MM-NilarNeural"
                    except Exception as tts_error:
                        data["tts_error"]=str(tts_error)[:500]
                    data["status"]="draft"
                    save_project(pid,uploaded.name,"draft",data)
                    status.update(label="Recap assets prepared",state="complete")
                st.success("Transcript, Burmese recap နဲ့ SRT draft ရပါပြီ။")
                if data.get("assets",{}).get("voice"):
                    st.success("AI narration ကိုလည်း အလိုအလျောက် ထုတ်ပြီးပါပြီ။ Video Editor မှာ preview / render ဆက်လုပ်နိုင်ပါတယ်။")
                else:
                    st.warning("Recap ကိုသိမ်းထားပါတယ်၊ ဒါပေမဲ့ AI voice မထွက်ပါ။ Voice Studio မှာ ပြန်စမ်းပါ သို့မဟုတ် audio upload လုပ်ပါ။")
                    if data.get("tts_error"): st.caption(data["tts_error"])
                st.rerun()
            except Exception as e:
                save_project(pid,uploaded.name,"failed",data,str(e))
                st.error("Processing failed: "+str(e))

elif page=="Settings":
    st.markdown("### API & provider settings")
    st.info("Persistent encrypted Save အတွက် hosting Environment Variables ထဲမှာ APP_ENCRYPTION_KEY သတ်မှတ်ပါ။ Key မသတ်မှတ်ထားလျှင် settings သည် session-only ဖြစ်ပြီး permanent success မပြပါ။ Public/shared app အတွက် authentication မပါသေးသောကြောင့် private deployment သာသုံးပါ။")
    with st.form("provider_settings"):
        groq=st.text_input("Groq API Key",value=st.session_state.api_groq,type="password")
        gemini=st.text_input("Gemini API Key",value=st.session_state.api_gemini,type="password")
        model=st.text_input("Preferred Gemini model",value=st.session_state.api_gemini_model)
        submitted=st.form_submit_button("Save settings",type="primary")
    if submitted:
        st.session_state.api_groq=groq.strip(); st.session_state.api_gemini=gemini.strip(); st.session_state.api_gemini_model=model.strip() or "gemini-3.8-flash"
        if encryption_box() is not None:
            try:
                save_encrypted_keys(st.session_state.api_groq,st.session_state.api_gemini)
                st.success("API keys saved encrypted to persistent server storage.")
            except Exception as e:
                st.error("Encrypted save failed: "+str(e))
        else:
            st.warning("Keys are available for this session only. Set APP_ENCRYPTION_KEY in hosting Environment Variables for encrypted persistent saving.")
    c1,c2=st.columns(2)
    c1.markdown("[Get Groq API key](https://console.groq.com/keys)")
    c2.markdown("[Get Gemini API key](https://aistudio.google.com/apikey)")
    if st.button("Test API connections"):
        if not st.session_state.api_groq or not st.session_state.api_gemini: st.error("API key နှစ်ခုလုံး ထည့်ပါ။")
        else:
            try:
                from groq import Groq
                gm=Groq(api_key=st.session_state.api_groq,timeout=20,max_retries=0)
                gm.models.list()
                names,client=gemini_models(st.session_state.api_gemini)
                st.success("Groq connection OK")
                if names: st.success("Gemini connection OK · "+", ".join(names[:8]))
                else: st.warning("Gemini key သည် မမှန်နိုင်ပါ သို့မဟုတ် model list access မရပါ။")
            except Exception as e: st.error("Connection test failed: "+str(e)[:500])

elif page in ("Subtitle Studio","Voice Studio","Video Editor","Export History"):
    rows=load_projects()
    if page=="Export History":
        st.markdown("### Export history")
        if not rows: st.info("Project မရှိသေးပါ။")
        for r in rows:
            data=json.loads(r["data"])
            with st.container(border=True):
                st.markdown("**"+r["title"]+"** · "+r["status"])
                if r["error"]: st.error(r["error"][:400])
                final=data.get("assets",{}).get("final")
                if final and Path(final).exists(): put_download("Download MP4",final,Path(final).name,"video/mp4")
                if st.button("Delete project",key="delete_"+r["id"]):
                    if st.session_state.get("confirm_delete")==r["id"]:
                        shutil.rmtree(PROJECTS/r["id"],ignore_errors=True)
                        with db() as con: con.execute("DELETE FROM projects WHERE id=?",(r["id"],)); con.commit()
                        st.session_state.confirm_delete=""; st.rerun()
                    else:
                        st.session_state.confirm_delete=r["id"]; st.warning("ဖျက်ရန် Delete project ကို ထပ်နှိပ်ပါ။")
    else:
        options={r["id"]:f"{r['title']} · {r['status']}" for r in rows}
        if not options: st.info("Project မရှိသေးပါ။ Create Recap မှာ စတင်ပါ။")
        else:
            selected=st.selectbox("Project",list(options),format_func=lambda x:options[x],index=list(options).index(st.session_state.active_project) if st.session_state.active_project in options else 0)
            st.session_state.active_project=selected
            row=load_project(selected); data=json.loads(row["data"]); assets=data.get("assets",{}); root=project_dir(selected)
            video=Path(data["video"]) if data.get("video") else None
            if page=="Subtitle Studio":
                st.markdown("### Subtitle Studio")
                srt_path=Path(assets["srt"]) if assets.get("srt") else root/"burmese_recap.srt"
                current=srt_path.read_text(encoding="utf-8") if srt_path.exists() else ""
                srt_upload=st.file_uploader("Import external SRT",type=["srt"],key="srt_import_"+selected)
                if srt_upload:
                    try: current=validate_srt(srt_upload.getvalue().decode("utf-8-sig"))
                    except Exception as e: st.error(str(e))
                if "recap" in assets and Path(assets["recap"]).exists():
                    recap=Path(assets["recap"]).read_text(encoding="utf-8")
                    with st.expander("Generate SRT draft from recap"):
                        chars=st.slider("Characters per line",10,45,25)
                        lines=st.slider("Max lines",1,3,2)
                        dur=float(data.get("meta",{}).get("duration",180))
                        if st.button("Regenerate SRT draft"):
                            current=make_srt(recap,dur,chars,lines)
                edited=st.text_area("SRT editor",value=current,height=400)
                if st.button("Save SRT",type="primary"):
                    try:
                        checked=validate_srt(edited); srt_path.write_text(checked,encoding="utf-8")
                        assets["srt"]=str(srt_path); data["assets"]=assets; save_project(selected,row["title"],"draft",data)
                        st.success("SRT saved")
                    except Exception as e: st.error(str(e))
                st.download_button("Download SRT",edited,file_name="burmese_recap.srt",mime="application/x-subrip",use_container_width=True)
                st.caption("SRT draft timing is approximate. Narration-based precise word alignment is not available in this first build.")
            elif page=="Voice Studio":
                st.markdown("### Voice Studio")
                recap_path=Path(assets["recap"]) if assets.get("recap") else None
                recap=recap_path.read_text(encoding="utf-8") if recap_path and recap_path.exists() else ""
                script=st.text_area("Narration script",value=recap,height=280)
                voice=st.selectbox("Myanmar voice",["my-MM-NilarNeural","my-MM-ThihaNeural"])
                speed=st.slider("Speaking speed (%)",-30,30,0)
                uploaded_voice=st.file_uploader("Or upload existing narration",type=["mp3","wav","m4a","aac"],key="voice_upload_"+selected)
                if st.button("Save script",key="save_script"):
                    if recap_path: recap_path.write_text(script,encoding="utf-8")
                    st.success("Script saved")
                if st.button("Generate narration",type="primary"):
                    try:
                        if uploaded_voice:
                            vp=root/("uploaded_voice"+Path(uploaded_voice.name).suffix.lower()); vp.write_bytes(uploaded_voice.getvalue())
                        else:
                            if not script.strip(): raise ValueError("Narration script အလွတ်ဖြစ်နေပါတယ်။")
                            vp=root/"narration.mp3"; create_voice(script,vp,voice,speed)
                        assets["voice"]=str(vp); data["assets"]=assets; data["voice_provider"]="uploaded audio" if uploaded_voice else "edge-tts "+voice
                        save_project(selected,row["title"],"draft",data)
                        st.success("Narration audio saved")
                    except Exception as e: st.error("Voice generation failed: "+str(e))
                vp=Path(assets["voice"]) if assets.get("voice") else None
                if vp and vp.exists():
                    st.audio(vp.read_bytes()); st.caption(f"Voice file: {vp.name} · {vp.stat().st_size/1024:.0f} KB")
                    put_download("Download narration",vp,vp.name,"audio/mpeg")
            elif page=="Video Editor":
                st.markdown("### Video Editor · live source preview")
                if video and video.exists():
                    st.video(str(video))
                    meta=data.get("meta",{})
                    st.caption(f"{meta.get('width','?')}×{meta.get('height','?')} · {meta.get('duration',0):.1f}s · {video.stat().st_size/1024/1024:.1f} MB")
                settings=data.setdefault("settings",{})
                settings.setdefault("subtitles_enabled",True); settings.setdefault("text_enabled",False); settings.setdefault("blur_enabled",False); settings.setdefault("logo_enabled",False)
                settings["audio_mode"]=st.selectbox("Audio output",["Narration only","Mix narration + original","Original audio only"],index=["Narration only","Mix narration + original","Original audio only"].index(settings.get("audio_mode","Narration only")))
                tool=st.radio("Adjust tool",["Subtitle","Text","Blur","Logo"],horizontal=True)
                if tool=="Subtitle":
                    settings["subtitles_enabled"]=st.checkbox("Enable subtitles",value=settings["subtitles_enabled"])
                    settings["subtitle_size"]=st.slider("Subtitle font size",12,48,int(settings.get("subtitle_size",22)))
                    st.caption("Subtitle → Subtitle Adjust")
                elif tool=="Text":
                    settings["text_enabled"]=st.checkbox("Enable text",value=settings["text_enabled"])
                    settings["overlay_text"]=st.text_input("Overlay text",value=settings.get("overlay_text",""))
                    settings["text_size"]=st.slider("Text size",8,120,int(settings.get("text_size",32)))
                    settings["text_color"]=st.color_picker("Text color",settings.get("text_color","#FFFFFF"))
                    settings["text_x"]=st.slider("X position",0,1000,int(settings.get("text_x",20)))
                    settings["text_y"]=st.slider("Y position",0,1000,int(settings.get("text_y",20)))
                    st.caption("Text → Text Adjust")
                elif tool=="Blur":
                    settings["blur_enabled"]=st.checkbox("Enable blur",value=settings["blur_enabled"])
                    settings["blur_x"]=st.slider("Blur X",0,1000,int(settings.get("blur_x",20)))
                    settings["blur_y"]=st.slider("Blur Y",0,1000,int(settings.get("blur_y",20)))
                    settings["blur_w"]=st.slider("Blur width",20,1000,int(settings.get("blur_w",200)))
                    settings["blur_h"]=st.slider("Blur height",20,600,int(settings.get("blur_h",80)))
                    settings["blur_strength"]=st.slider("Blur intensity",1,30,int(settings.get("blur_strength",8)))
                    st.caption("Blur → Blur Adjust")
                else:
                    settings["logo_enabled"]=st.checkbox("Enable logo",value=settings["logo_enabled"])
                    logo=st.file_uploader("Upload transparent PNG logo",type=["png"],key="logo_"+selected)
                    if logo:
                        lp=root/"logo.png"; lp.write_bytes(logo.getvalue()); settings["logo_path"]=str(lp)
                    settings["logo_x"]=st.slider("Logo X",0,1000,int(settings.get("logo_x",20)))
                    settings["logo_y"]=st.slider("Logo Y",0,1000,int(settings.get("logo_y",20)))
                    settings["logo_size"]=st.slider("Logo width (% of original image)",5,100,int(settings.get("logo_size",20)))
                    settings["logo_opacity"]=st.slider("Logo opacity",0.1,1.0,float(settings.get("logo_opacity",1.0)))
                    st.caption("Logo → Logo Adjust")
                if st.button("Save editor settings"):
                    data["settings"]=settings; save_project(selected,row["title"],row["status"],data); st.success("Settings saved")
                st.markdown("#### Edited preview")
                if st.button("▶ Generate preview (first 8 seconds)"):
                    voice_asset=Path(assets["voice"]) if assets.get("voice") else None
                    if not voice_asset or not voice_asset.exists():
                        st.warning("Preview အတွက် Voice Studio မှာ narration generate/upload လုပ်ပါ။")
                    else:
                        try:
                            preview_path=root/"preview.mp4"
                            subtitle_asset=Path(assets["srt"]) if assets.get("srt") and Path(assets["srt"]).exists() else None
                            with st.spinner("Preview render လုပ်နေသည်…"):
                                render_video(video,voice_asset,preview_path,subtitle_asset,settings,preview=True)
                            data["assets"]["preview"]=str(preview_path)
                            data["settings"]=settings
                            save_project(selected,row["title"],row["status"],data)
                            st.video(preview_path.read_bytes())
                        except Exception as e:
                            st.error("Preview render failed: "+str(e))
                if assets.get("preview") and Path(assets["preview"]).exists():
                    st.caption("Last generated preview. Generate again after changing settings.")
                    st.video(Path(assets["preview"]).read_bytes())
                if st.button("DONE — CONFIRM EDITS",type="primary"):
                    data["settings"]=settings; data["confirmed_settings"]=json.loads(json.dumps(settings))
                    save_project(selected,row["title"],"confirmed",data); st.success("Edit settings confirmed. Reopen or change settings and confirm again to update.")
                st.markdown("#### Render final MP4")
                st.caption("Use a short test video first. Final render begins only after confirmation.")
                if st.button("🎞️ RENDER FINAL MP4",type="primary"):
                    if "confirmed_settings" not in data: st.error("အရင် DONE — CONFIRM EDITS ကိုနှိပ်ပါ။")
                    elif not video or not video.exists(): st.error("Original video file မတွေ့ပါ။")
                    elif not assets.get("voice") or not Path(assets["voice"]).exists(): st.error("Voice Studio မှာ narration generate/upload လုပ်ပါ။")
                    else:
                        try:
                            sp=Path(assets["srt"]) if assets.get("srt") and Path(assets["srt"]).exists() else None
                            out=root/"final.mp4"
                            with st.spinner("FFmpeg rendering…"):
                                render_video(video,Path(assets["voice"]),out,sp,data["confirmed_settings"])
                            assets["final"]=str(out); data["assets"]=assets
                            save_project(selected,row["title"],"completed",data)
                            st.success("Final MP4 rendered and validated")
                            st.video(out.read_bytes()); put_download("Download final MP4",out,out.name,"video/mp4")
                        except Exception as e:
                            save_project(selected,row["title"],"failed",data,str(e)); st.error("Render failed: "+str(e))
