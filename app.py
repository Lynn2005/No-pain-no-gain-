import os
import re
import json
import uuid
import time
import shutil
import sqlite3
import subprocess
import socket
import ipaddress
from urllib.parse import urlsplit, urljoin
from pathlib import Path
from datetime import datetime
import requests
from flask import Flask, request, redirect, url_for, render_template_string, send_file

APP_NAME = "ONE CLICK — ONE MOVIE RECAP"
BASE = Path(os.getenv("APP_DATA_DIR", "data")).resolve()
UPLOADS = BASE / "uploads"
PROJECTS = BASE / "projects"
for p in (BASE, UPLOADS, PROJECTS):
    p.mkdir(parents=True, exist_ok=True)
DB = BASE / "recap.db"

def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS projects(
        id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
        status TEXT NOT NULL, data TEXT NOT NULL, error TEXT DEFAULT '')""")
    con.commit()
    return con

def save_project(pid, title, status, data, error=""):
    with db() as con:
        con.execute("INSERT INTO projects(id,title,created_at,status,data,error) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title,status=excluded.status,data=excluded.data,error=excluded.error",
                    (pid,title,datetime.utcnow().isoformat(timespec="seconds")+"Z",status,json.dumps(data,ensure_ascii=False),error))
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

MAX_REMOTE_VIDEO_BYTES = 200 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
VIDEO_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/x-msvideo": ".avi",
    "application/octet-stream": "",
}

def validate_public_video_url(raw_url):
    """Allow only public HTTP(S) hosts; reject local/private targets and unusual ports."""
    parsed = urlsplit((raw_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("မှန်ကန်တဲ့ http/https video file URL ထည့်ပါ။")
    if parsed.username or parsed.password:
        raise ValueError("URL ထဲမှာ username/password ထည့်ထားတာကို လက်မခံပါ။")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("URL port မမှန်ပါ။")
    if port not in (None, 80, 443):
        raise ValueError("80 သို့မဟုတ် 443 port ကိုသာ အသုံးပြုနိုင်ပါတယ်။")
    host = parsed.hostname.rstrip(".").lower()
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local network URL ကို လက်မခံပါ။")
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise ValueError("URL domain ကို မရှာတွေ့ပါ။")
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Public internet ပေါ်က URL ကိုသာ အသုံးပြုနိုင်ပါတယ်။")
    return parsed.geturl()

def download_direct_video(raw_url, target_dir):
    """Download a direct video-file URL only (not streaming pages), capped at 200 MB."""
    current_url = validate_public_video_url(raw_url)
    response = None
    for _ in range(6):
        response = requests.get(current_url, stream=True, timeout=(10, 45), allow_redirects=False,
                                headers={"User-Agent": "NoPainNoGainRecap/1.0"})
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Video URL redirect မမှန်ပါ။")
            current_url = validate_public_video_url(urljoin(current_url, location))
            continue
        if response.status_code != 200:
            status_code = response.status_code
            response.close()
            raise ValueError(f"Video URL က HTTP {status_code} ပြန်ပေးပါတယ်။")
        break
    else:
        raise ValueError("Video URL redirect အဆင့်များလွန်းပါတယ်။")

    try:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type and not (content_type.startswith("video/") or content_type == "application/octet-stream"):
            raise ValueError("ဒီ URL က တိုက်ရိုက် video file မဟုတ်ပါ။ MP4/MOV/MKV/WEBM file link ကို ထည့်ပါ။")
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_REMOTE_VIDEO_BYTES:
            raise ValueError("Video ဖိုင်က 200 MB ထက်ကြီးနေပါတယ်။ Short clip သို့မဟုတ် သေးတဲ့ဖိုင်ကို သုံးပါ။")
        path_ext = Path(urlsplit(current_url).path).suffix.lower()
        mime_ext = VIDEO_MIME_EXTENSIONS.get(content_type, "")
        extension = path_ext if path_ext in VIDEO_EXTENSIONS else mime_ext
        if extension not in VIDEO_EXTENSIONS:
            raise ValueError("Video ဖိုင်အမျိုးအစား မသိနိုင်ပါ။ URL က .mp4/.mov/.mkv/.webm/.avi/.m4v တစ်ခုဖြစ်ရပါမယ်။")
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / ("movie" + extension)
        total = 0
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_REMOTE_VIDEO_BYTES:
                    raise ValueError("Video ဖိုင်က 200 MB ထက်ကြီးနေပါတယ်။ Short clip သို့မဟုတ် သေးတဲ့ဖိုင်ကို သုံးပါ။")
                output.write(chunk)
        if total == 0:
            raise ValueError("Video ဖိုင်ဗလာဖြစ်နေပါတယ်။")
        return destination, "URL video" + extension
    except Exception:
        try:
            (Path(target_dir) / ("movie" + (extension if "extension" in locals() else ".mp4"))).unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        response.close()

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

def generate_recap(transcript,api_key,requested_model,style,groq_api_key=None):
    length_instruction={"တို":"တိုတောင်းသော recap","အလယ်အလတ်":"အလယ်အလတ်အရှည် recap","ရှည်":"အသေးစိတ်ရှည်လျားသော recap"}.get(style,"အလယ်အလတ်အရှည် recap")
    chunks=[transcript[i:i+18000] for i in range(0,len(transcript),18000)]
    def make_chunk_prompt(idx,chunk):
        return ("You are a careful movie-recap writer. Extract chronological plot facts, characters, motivations, key reveals, climax and ending. "
                "Do not invent facts. Return concise notes in Burmese. This is chunk "+str(idx)+" of "+str(len(chunks))+".\n\n"+chunk)
    def make_final_prompt(notes):
        return ("အောက်ပါ plot notes ကို အခြေခံ၍ မြန်မာဘာသာဖြင့် သဘာဝကျပြီး နားထောင်ကောင်းသော movie recap narration ရေးပါ။ "
                "အစ၊ အလယ်၊ အထွတ်အထိပ်၊ အဆုံးကို အစဉ်လိုက်ထိန်းပါ။ မူရင်းတွင်မရှိသောအဖြစ်အပျက် မတီထွင်ပါနှင့်။ "
                "ထပ်နေသောအကြောင်းအရာဖယ်ပါ။ "+length_instruction+" ဖြစ်ရမည်။ ခေါင်းစဉ်/မှတ်ချက်မထည့်ဘဲ ဖတ်ပြမည့်စာသားသာ ပြန်ပါ。\n\n".replace("。","。")+
                "\n\n".join(notes)[:70000])
    gemini_error=None
    try:
        models,client=gemini_models(api_key)
        if requested_model in models:
            model=requested_model
        else:
            preferred=[m for m in models if "flash" in m.lower() and "preview" not in m.lower()]
            if not preferred:
                raise RuntimeError("Gemini API မှာ အသုံးပြုနိုင်တဲ့ text model မတွေ့ပါ။")
            model=preferred[0]
        notes=[]
        for idx,chunk in enumerate(chunks,1):
            resp=client.models.generate_content(model=model,contents=make_chunk_prompt(idx,chunk))
            notes.append(resp.text or "")
        resp=client.models.generate_content(model=model,contents=make_final_prompt(notes))
        recap=(resp.text or "").strip()
        if not recap:
            raise RuntimeError("Gemini က recap စာသားအလွတ် ပြန်ပေးပါတယ်။")
        return recap,model
    except Exception as exc:
        gemini_error=exc
    if not groq_api_key:
        raise RuntimeError("Gemini recap မအောင်မြင်ပါ။ Groq API Key မရှိသောကြောင့် fallback မလုပ်နိုင်ပါ။ Gemini error: "+str(gemini_error))
    try:
        from groq import Groq
        gc=Groq(api_key=groq_api_key,timeout=180,max_retries=1)
        available=[]
        try:
            available=[getattr(m,"id","") for m in gc.models.list().data]
        except Exception:
            pass
        preferred=["llama-3.3-70b-versatile","openai/gpt-oss-120b","llama-3.1-8b-instant","qwen/qwen3-32b"]
        candidates=[m for m in preferred if not available or m in available]
        candidates += [m for m in available if m and any(k in m.lower() for k in ("llama","qwen","gpt-oss")) and not any(k in m.lower() for k in ("whisper","guard","embed")) and m not in candidates]
        if not candidates:
            raise RuntimeError("Groq account မှာ recap အတွက် text model မတွေ့ပါ။")
        errors=[]
        for groq_model in candidates:
            try:
                notes=[]
                for idx,chunk in enumerate(chunks,1):
                    response=gc.chat.completions.create(
                        model=groq_model,
                        messages=[{"role":"system","content":"You are a careful movie-recap writer. Do not invent plot facts. Respond in natural Burmese."},
                                  {"role":"user","content":make_chunk_prompt(idx,chunk)}],
                        temperature=0.3,max_tokens=2048)
                    notes.append((response.choices[0].message.content or "").strip())
                response=gc.chat.completions.create(
                    model=groq_model,
                    messages=[{"role":"system","content":"မြန်မာဘာသာဖြင့် သဘာဝကျသော movie recap narration ရေးပါ။ မူရင်းတွင်မရှိသောအဖြစ်အပျက် မတီထွင်ပါနှင့်။"},
                              {"role":"user","content":make_final_prompt(notes)}],
                    temperature=0.5,max_tokens=4096)
                recap=(response.choices[0].message.content or "").strip()
                if recap:
                    return recap,"Groq fallback · "+groq_model
                errors.append(groq_model+": empty response")
            except Exception as model_error:
                errors.append(groq_model+": "+str(model_error)[:180])
        raise RuntimeError("Groq fallback models မအောင်မြင်ပါ: "+" | ".join(errors))
    except Exception as groq_error:
        raise RuntimeError("Gemini quota/API error ဖြစ်ပြီး Groq fallback လည်း မအောင်မြင်ပါ။ Gemini: "+str(gemini_error)[:350]+" | Groq: "+str(groq_error)[:500])

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
        vw=max(2,int(meta.get("width") or 1280)); vh=max(2,int(meta.get("height") or 720))
        x=max(0,min(vw-2,int(settings.get("blur_x",20)))); y=max(0,min(vh-2,int(settings.get("blur_y",20))))
        w=max(2,min(vw-x,int(settings.get("blur_w",200)))); h=max(2,min(vh-y,int(settings.get("blur_h",80))))
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


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 210 * 1024 * 1024

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title or "ONE CLICK — ONE MOVIE RECAP" }}</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top right,#20203a 0,#101117 45%,#0b0c10 100%);color:#f3f3fa;font:16px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:1000px;margin:auto;padding:24px 16px 60px}.hero,.panel{border:1px solid #353548;border-radius:18px;background:#171821;padding:22px;margin:0 0 18px}.hero{background:linear-gradient(120deg,#191927,#27213e 65%,#172438)}h1{font-size:clamp(1.5rem,5vw,2.4rem);margin:0}h2{margin-top:0;font-size:1.25rem}p{color:#c9c9d9}.muted{color:#a7a8ba;font-size:.9rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.field{margin:12px 0}label{display:block;font-weight:650;margin-bottom:6px}input,select,textarea{width:100%;padding:12px;border-radius:10px;border:1px solid #44465a;background:#0e1018;color:#fff;font:inherit}textarea{min-height:210px;resize:vertical}button,.btn{display:inline-block;border:0;border-radius:10px;padding:12px 16px;background:#8b78ff;color:white;font-weight:750;font-size:1rem;text-decoration:none;cursor:pointer}button:hover,.btn:hover{filter:brightness(1.1)}.secondary{background:#303143}.error{border:1px solid #8e3e4b;background:#351c24;padding:12px;border-radius:10px;color:#ffc5cc}.ok{border:1px solid #286b57;background:#142b25;padding:12px;border-radius:10px}.card{padding:14px;border:1px solid #353548;border-radius:12px;margin:10px 0}.actions{display:flex;gap:10px;flex-wrap:wrap}.pill{font-size:.8rem;background:#2a2b3a;border-radius:99px;padding:4px 9px;color:#d9d8f4}a{color:#c8beff}small{color:#b4b5c7}hr{border:0;border-top:1px solid #353548;margin:20px 0}
</style></head><body><main>
<header class="hero"><h1>🎬 ONE CLICK — ONE MOVIE RECAP</h1><p>Upload a video or direct video URL · Generate Burmese recap, subtitles and narration.</p><span class="pill">Render Web App</span></header>
{% if msg %}<div class="ok">{{ msg }}</div>{% endif %}
{% if error %}<div class="error">{{ error }}</div>{% endif %}
{{ body|safe }}
<footer class="muted">Free hosting uses temporary storage. Download your files after processing; they may disappear after restart or redeploy.</footer>
</main></body></html>"""

HOME = r"""
<section class="panel"><h2>🚀 Create a recap</h2>
<p>Direct downloadable video files only (MP4, MOV, MKV, WEBM, AVI, M4V), up to 200 MB. YouTube/Netflix pages are not direct video files.</p>
<form method="post" action="/create" enctype="multipart/form-data">
<div class="grid">
<div class="field"><label for="movie">Upload video file</label><input id="movie" name="movie" type="file" accept=".mp4,.mov,.mkv,.webm,.avi,.m4v,video/*"></div>
<div class="field"><label for="video_url">Or direct video URL</label><input id="video_url" name="video_url" type="url" placeholder="https://example.com/video.mp4"></div>
</div>
<div class="grid">
<div class="field"><label for="style">Recap length</label><select id="style" name="style"><option>တို</option><option selected>အလယ်အလတ်</option><option>ရှည်</option></select></div>
<div class="field"><label for="model">Whisper model</label><select id="model" name="model"><option>whisper-large-v3-turbo</option><option>whisper-large-v3</option></select></div>
<div class="field"><label for="gemini_model">Gemini model (optional)</label><input id="gemini_model" name="gemini_model" value="{{ default_model }}"></div>
</div>
<hr><h2>API keys</h2><p class="muted">Render Environment Variables (GROQ_API_KEY and GEMINI_API_KEY) are recommended. Keys entered here are used for this request only and are not saved to the database.</p>
<div class="grid"><div class="field"><label for="groq_key">Groq API key</label><input id="groq_key" name="groq_key" type="password" autocomplete="off" placeholder="Use Render environment variable if configured"></div>
<div class="field"><label for="gemini_key">Gemini API key</label><input id="gemini_key" name="gemini_key" type="password" autocomplete="off" placeholder="Use Render environment variable if configured"></div></div>
<button type="submit">🚀 ONE CLICK RECAP</button></form></section>
<section class="panel"><h2>📁 Recent projects</h2>
{% if projects %}{% for p in projects %}<div class="card"><strong>{{ p["title"] }}</strong> <span class="pill">{{ p["status"] }}</span><p class="muted">{{ p["created_at"] }} · {{ p["id"] }}</p>{% if p["error"] %}<p>{{ p["error"][:250] }}</p>{% endif %}<a class="btn secondary" href="/project/{{ p['id'] }}">Open project</a></div>{% endfor %}{% else %}<p>No projects yet. Upload a short clip to get started.</p>{% endif %}
</section>
<section class="panel"><h2>⚙️ Render configuration</h2><p>Set <code>GROQ_API_KEY</code> and <code>GEMINI_API_KEY</code> in Render → your service → Environment for the simplest setup. FFmpeg/FFprobe are installed by the Docker image.</p><p class="muted">This public demo has no login. Anyone with the URL may submit jobs and use server resources. Do not enter private information or deploy personal keys to a shared public app.</p></section>
"""

PROJECT = r"""
<div class="panel"><div class="actions"><a class="btn secondary" href="/">← Home</a><a class="btn secondary" href="/download/{{ pid }}/video">Download source video</a></div>
<h2>{{ row["title"] }}</h2><p><span class="pill">{{ row["status"] }}</span> · {{ row["created_at"] }}</p>
{% if row["error"] %}<div class="error">{{ row["error"] }}</div>{% endif %}
{% if data.get("meta") %}<p class="muted">{{ data["meta"].get("width","?") }}×{{ data["meta"].get("height","?") }} · {{ "%.1f"|format(data["meta"].get("duration",0)) }} seconds</p>{% endif %}
{% if assets.get("transcript") and exists(assets.get("transcript")) %}<div class="card"><h2>Original transcript</h2><a class="btn secondary" href="/download/{{ pid }}/transcript">Download transcript TXT</a></div>{% endif %}
{% if assets.get("recap") and exists(assets.get("recap")) %}
<form method="post" action="/project/{{ pid }}/script" class="card"><h2>မြန်မာ Movie Recap Script</h2><textarea name="script">{{ read_text(assets["recap"]) }}</textarea><button type="submit">Save script + regenerate SRT</button></form>
{% endif %}
{% if assets.get("srt") and exists(assets.get("srt")) %}<div class="card"><h2>Subtitles</h2><a class="btn secondary" href="/download/{{ pid }}/srt">Download SRT</a><p class="muted">Subtitle timing is a draft estimate; review before publishing.</p></div>{% endif %}
{% if assets.get("voice") and exists(assets.get("voice")) %}<div class="card"><h2>Narration audio</h2><audio controls style="width:100%" src="/download/{{ pid }}/voice"></audio><p><a class="btn secondary" href="/download/{{ pid }}/voice">Download narration</a></p></div>{% endif %}
<div class="panel"><h2>🎙️ Generate Burmese narration</h2><form method="post" action="/project/{{ pid }}/voice"><div class="grid"><div class="field"><label>Voice</label><select name="voice"><option>my-MM-NilarNeural</option><option>my-MM-ThihaNeural</option></select></div><div class="field"><label>Speed (%)</label><input type="number" name="speed" value="0" min="-30" max="30"></div></div><button type="submit">Generate narration</button></form></div>
<div class="panel"><h2>🎞️ Render final MP4</h2><form method="post" action="/project/{{ pid }}/render"><div class="grid"><div class="field"><label>Audio output</label><select name="audio_mode"><option>Narration only</option><option>Mix narration + original</option><option>Original audio only</option></select></div><div class="field"><label>Subtitle font size</label><input type="number" name="subtitle_size" value="22" min="12" max="48"></div></div><label><input type="checkbox" name="subtitles_enabled" checked style="width:auto"> Enable subtitles</label><p><button type="submit">Render final MP4</button></p></form>
{% if assets.get("final") and exists(assets.get("final")) %}<a class="btn" href="/download/{{ pid }}/final">Download final MP4</a><video controls style="width:100%;margin-top:12px" src="/download/{{ pid }}/final"></video>{% endif %}
</div></div>
"""

def page(body, title=None, msg="", error="", **context):
    return render_template_string(PAGE, body=render_template_string(body, **context), title=title,
                                  msg=msg, error=error)

def project_assets(pid):
    if not re.fullmatch(r"[a-f0-9]{12}", pid or ""):
        raise ValueError("Project ID မမှန်ပါ။")
    row=load_project(pid)
    if row is None:
        raise LookupError("Project မတွေ့ပါ။")
    data=json.loads(row["data"])
    return row,data,data.get("assets",{}),project_dir(pid)

@app.get("/")
def home():
    projects=load_projects()
    return page(HOME, projects=projects[:12], default_model=os.getenv("GEMINI_MODEL","gemini-3.8-flash"))

@app.post("/create")
def create():
    uploaded=request.files.get("movie")
    movie_url=(request.form.get("video_url") or "").strip()
    groq_key=(request.form.get("groq_key") or os.getenv("GROQ_API_KEY","")).strip()
    gemini_key=(request.form.get("gemini_key") or os.getenv("GEMINI_API_KEY","")).strip()
    model=(request.form.get("gemini_model") or os.getenv("GEMINI_MODEL","gemini-3.8-flash")).strip()
    style=request.form.get("style","အလယ်အလတ်")
    groq_model=request.form.get("model","whisper-large-v3-turbo")
    if not groq_key or not gemini_key:
        return page(HOME, error="Groq နဲ့ Gemini API Key နှစ်ခုလုံးလိုအပ်ပါတယ်။ Render Environment Variables ထဲမှာ သတ်မှတ်နိုင်ပါတယ်။",
                    projects=load_projects()[:12],default_model=model), 400
    if not (uploaded and uploaded.filename) and not movie_url:
        return page(HOME,error="Video file တင်ပါ သို့မဟုတ် direct video URL ထည့်ပါ။",projects=load_projects()[:12],default_model=model),400
    if not ffmpeg_ok():
        return page(HOME,error="FFmpeg/FFprobe မတွေ့ပါ။ Docker image ကို စစ်ပါ။",projects=load_projects()[:12],default_model=model),500
    pid=uuid.uuid4().hex[:12]
    root=project_dir(pid)
    movie_name=""
    try:
        if uploaded and uploaded.filename:
            extension=Path(uploaded.filename).suffix.lower()
            if extension not in VIDEO_EXTENSIONS:
                raise ValueError("MP4/MOV/MKV/WEBM/AVI/M4V video ကိုသာ တင်ပါ။")
            original=root/("movie"+extension)
            uploaded.save(original)
            if original.stat().st_size > MAX_REMOTE_VIDEO_BYTES:
                original.unlink(missing_ok=True)
                raise ValueError("Video ဖိုင်က 200 MB ထက်ကြီးနေပါတယ်။")
            movie_name=Path(uploaded.filename).name[:180]
        else:
            original,movie_name=download_direct_video(movie_url,root)
        data={"video":str(original),"assets":{},"settings":{"subtitles_enabled":True,"text_enabled":False,"blur_enabled":False,"logo_enabled":False},"style":style}
        save_project(pid,movie_name,"processing",data)
        meta=probe_video(original); data["meta"]=meta
        if not meta["has_audio"]:
            raise RuntimeError("ဒီ video မှာ audio stream မရှိပါ။ Audio ပါတဲ့ video ကိုသုံးပါ။")
        audio=root/"source_audio.mp3"
        run_cmd(["ffmpeg","-y","-i",str(original),"-vn","-ac","1","-ar","16000","-b:a","64k",str(audio)],timeout=900)
        data["assets"]["source_audio"]=str(audio)
        transcript,segments=transcribe(audio,groq_key,groq_model)
        tpath=root/"original_transcript.txt"; tpath.write_text(transcript,encoding="utf-8")
        data["assets"]["transcript"]=str(tpath); data["transcript_segments"]=segments
        recap,actual_model=generate_recap(transcript,gemini_key,model,style,groq_key)
        rpath=root/"burmese_recap.txt"; rpath.write_text(recap,encoding="utf-8")
        data["assets"]["recap"]=str(rpath); data["gemini_model_used"]=actual_model
        srtpath=root/"burmese_recap.srt"; srtpath.write_text(make_srt(recap,meta["duration"]),encoding="utf-8")
        data["assets"]["srt"]=str(srtpath)
        try:
            voicepath=root/"narration.mp3"
            create_voice(recap,voicepath,"my-MM-NilarNeural",0)
            data["assets"]["voice"]=str(voicepath); data["voice_provider"]="edge-tts my-MM-NilarNeural"
        except Exception as exc:
            data["tts_error"]=str(exc)[:500]
        save_project(pid,movie_name,"draft",data)
        return redirect(url_for("project_view",pid=pid, msg="Recap generated successfully."))
    except Exception as exc:
        save_project(pid,movie_name or "Movie recap","failed",data if "data" in locals() else {},str(exc)[:1800])
        return page(HOME,error="Processing failed: "+str(exc)[:900],projects=load_projects()[:12],default_model=model),500

@app.get("/project/<pid>")
def project_view(pid):
    try:
        row,data,assets,root=project_assets(pid)
        return page(PROJECT,title=row["title"],msg=request.args.get("msg",""),pid=pid,row=row,data=data,assets=assets,
                    exists=lambda p: bool(p and Path(p).is_file()),
                    read_text=lambda p: Path(p).read_text(encoding="utf-8") if p and Path(p).is_file() else "")
    except (ValueError,LookupError) as exc:
        return page('<section class="panel"><h2>Project error</h2><p>{{ error }}</p><a href="/">Home</a></section>',error=str(exc)),404

@app.post("/project/<pid>/script")
def save_script(pid):
    try:
        row,data,assets,root=project_assets(pid)
        script=(request.form.get("script") or "").strip()
        if not script: raise ValueError("Recap script အလွတ်မဖြစ်ရပါ။")
        path=Path(assets.get("recap",str(root/"burmese_recap.txt")))
        path.write_text(script,encoding="utf-8")
        srt=root/"burmese_recap.srt"; srt.write_text(make_srt(script,float(data.get("meta",{}).get("duration",180))),encoding="utf-8")
        assets.update({"recap":str(path),"srt":str(srt)}); data["assets"]=assets
        save_project(pid,row["title"],"draft",data)
        return redirect(url_for("project_view",pid=pid,msg="Script saved and SRT regenerated."))
    except Exception as exc:
        return page('<section class="panel"><p>{{ error }}</p><a href="/">Home</a></section>',error=str(exc)),400

@app.post("/project/<pid>/voice")
def project_voice(pid):
    try:
        row,data,assets,root=project_assets(pid)
        recap_path=Path(assets.get("recap",""))
        if not recap_path.is_file(): raise ValueError("Recap script မတွေ့ပါ။")
        voice=request.form.get("voice","my-MM-NilarNeural")
        if voice not in ("my-MM-NilarNeural","my-MM-ThihaNeural"): raise ValueError("Voice option မမှန်ပါ။")
        speed=max(-30,min(30,int(request.form.get("speed",0))))
        out=root/"narration.mp3"; create_voice(recap_path.read_text(encoding="utf-8"),out,voice,speed)
        assets["voice"]=str(out); data["assets"]=assets; data["voice_provider"]="edge-tts "+voice
        save_project(pid,row["title"],"draft",data)
        return redirect(url_for("project_view",pid=pid,msg="Narration generated."))
    except Exception as exc:
        return page('<section class="panel"><p>{{ error }}</p><a href="/">Home</a></section>',error="Voice generation failed: "+str(exc)),400

@app.post("/project/<pid>/render")
def project_render(pid):
    try:
        row,data,assets,root=project_assets(pid)
        video=Path(data.get("video",""))
        voice=Path(assets.get("voice",""))
        if not video.is_file(): raise ValueError("Source video မတွေ့ပါ။")
        if not voice.is_file(): raise ValueError("အရင် narration generate လုပ်ပါ။")
        subtitle=Path(assets["srt"]) if assets.get("srt") and Path(assets["srt"]).is_file() else None
        settings=data.setdefault("settings",{})
        settings["audio_mode"]=request.form.get("audio_mode","Narration only")
        if settings["audio_mode"] not in ("Narration only","Mix narration + original","Original audio only"):
            settings["audio_mode"]="Narration only"
        settings["subtitles_enabled"]="subtitles_enabled" in request.form
        settings["subtitle_size"]=max(12,min(48,int(request.form.get("subtitle_size",22))))
        out=root/"final.mp4"
        render_video(video,voice,out,subtitle,settings)
        assets["final"]=str(out); data["assets"]=assets
        save_project(pid,row["title"],"completed",data)
        return redirect(url_for("project_view",pid=pid,msg="Final MP4 rendered successfully."))
    except Exception as exc:
        try: save_project(pid,row["title"],"failed",data,str(exc)[:1000])
        except Exception: pass
        return page('<section class="panel"><h2>Render failed</h2><p>{{ error }}</p><a href="/project/{{ pid }}">Back to project</a></section>',error=str(exc)[:1000],pid=pid),500

@app.get("/download/<pid>/<asset>")
def download(pid,asset):
    allowed={"video":"video","transcript":"transcript","recap":"recap","srt":"srt","voice":"voice","final":"final"}
    if asset not in allowed: return page('<section class="panel">Unknown download <a href="/">Home</a></section>'),404
    try:
        row,data,assets,root=project_assets(pid)
        path=Path(data.get("video","")) if asset=="video" else Path(assets.get(allowed[asset],""))
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise LookupError("ဖိုင်မတွေ့ပါ။")
        mime={"video":"video/mp4","transcript":"text/plain","recap":"text/plain","srt":"application/x-subrip","voice":"audio/mpeg","final":"video/mp4"}[asset]
        return send_file(path,as_attachment=asset not in ("voice",),download_name=path.name,mimetype=mime)
    except (ValueError,LookupError) as exc:
        return page('<section class="panel"><p>{{ error }}</p><a href="/">Home</a></section>',error=str(exc)),404

@app.errorhandler(413)
def too_large(_):
    return page('<section class="panel"><h2>File too large</h2><p>Video size limit is 200 MB.</p><a href="/">Go back</a></section>'),413

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
