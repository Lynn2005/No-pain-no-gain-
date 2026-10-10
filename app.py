import os, re, subprocess, tempfile
from pathlib import Path
import streamlit as st
from groq import Groq
from google import genai
from streamlit_local_storage import LocalStorage

st.set_page_config(page_title="One-Click Movie Recap", page_icon="🎬", layout="wide")
st.markdown("""
<style>
.block-container{max-width:1100px;padding-top:1.5rem}
.hero{padding:1.4rem;border-radius:18px;background:linear-gradient(120deg,#171b2b,#333c61);color:white}
.stButton>button{border-radius:10px;font-weight:700}
</style>
<div class="hero"><h1>🎬 One-Click AI Movie Recap</h1><p>Movie upload → Burmese recap → SRT → Final MP4</p></div>
""", unsafe_allow_html=True)

def run(cmd):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError((p.stderr or "Command failed")[-1800:])
    return p.stdout.strip()

def srt_time(seconds):
    ms=int(max(0,seconds)*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_srt(text, duration):
    chunks=[x.strip() for x in re.split(r'(?<=[။!?])\s*|\n+',text) if x.strip()]
    if not chunks: chunks=[text.strip()]
    step=max(2.0,duration/max(1,len(chunks)))
    out=[]
    for i,line in enumerate(chunks):
        start=i*step; end=min(duration,start+step)
        out += [str(i+1),f"{srt_time(start)} --> {srt_time(max(start+0.5,end))}",line,""]
    return "\n".join(out)

def get_key(name, secret_name):
    try: return st.secrets.get("api",{}).get(secret_name) or os.getenv(name,"")
    except Exception: return os.getenv(name,"")

# API keys can be saved in this browser's local storage (not on the server).
try:
    local_storage = LocalStorage()
    saved_groq = local_storage.getItem("no_pain_groq_api_key", key="load_no_pain_groq_key") or st.session_state.get("load_no_pain_groq_key")
    saved_gemini = local_storage.getItem("no_pain_gemini_api_key", key="load_no_pain_gemini_key") or st.session_state.get("load_no_pain_gemini_key")
    if saved_groq and "groq_api_input" not in st.session_state:
        st.session_state["groq_api_input"] = saved_groq
    if saved_gemini and "gemini_api_input" not in st.session_state:
        st.session_state["gemini_api_input"] = saved_gemini
except Exception:
    local_storage = None

with st.sidebar:
    st.header("🔑 API Settings")
    st.text_input("Groq API Key",key="groq_api_input",value=st.session_state.get("groq_api_input",get_key("GROQ_API_KEY","groq_api_key")),type="password",help="Save လုပ်လျှင် ဒီ browser/device ထဲတွင် သိမ်းထားမည်။")
    st.text_input("Gemini API Key",key="gemini_api_input",value=st.session_state.get("gemini_api_input",get_key("GEMINI_API_KEY","gemini_api_key")),type="password")
    groq_key = st.session_state.get("groq_api_input","") or get_key("GROQ_API_KEY","groq_api_key")
    gemini_key = st.session_state.get("gemini_api_input","") or get_key("GEMINI_API_KEY","gemini_api_key")
    if st.button("💾 Save Keys",use_container_width=True):
        if not groq_key and not gemini_key:
            st.warning("သိမ်းရန် API Key အနည်းဆုံးတစ်ခု ထည့်ပါ။")
        elif local_storage is None:
            st.error("Local storage မရနိုင်ပါ။ App ကို reload လုပ်ပြီး ထပ်စမ်းပါ။")
        else:
            try:
                if groq_key:
                    local_storage.setItem("no_pain_groq_api_key",groq_key)
                if gemini_key:
                    local_storage.setItem("no_pain_gemini_api_key",gemini_key)
                st.success("API Keys ကို ဒီ browser ထဲမှာ သိမ်းပြီးပါပြီ။")
            except Exception as e:
                st.error(f"Key သိမ်းမရပါ: {e}")
    model=st.selectbox("Gemini model",["gemini-3.8-flash","gemini-3.7-flash","gemini-3.5-flash-lite"],index=0)
    st.caption("Keys များကို GitHub သို့မဟုတ် server ပေါ်မသိမ်းပါ။ Browser data ရှင်းလျှင် ပြန်ထည့်ရနိုင်သည်။")

st.markdown("### 1. Upload your movie")
video=st.file_uploader("Video file",type=["mp4","mov","mkv","webm","avi"])
if video:
    st.video(video)
    if st.button("🚀 Generate Burmese Recap",type="primary",use_container_width=True):
        if not groq_key or not gemini_key:
            st.error("Groq နဲ့ Gemini API Key နှစ်ခုလုံး ထည့်ပါ။")
        else:
            try:
                with tempfile.TemporaryDirectory(prefix="recap_") as td:
                    root=Path(td); vpath=root/("source"+Path(video.name).suffix.lower())
                    vpath.write_bytes(video.getvalue())
                    audio=root/"audio.mp3"
                    with st.status("Processing video…",expanded=True) as status:
                        st.write("Audio ထုတ်ယူနေသည်…")
                        run(["ffmpeg","-y","-i",str(vpath),"-vn","-ac","1","-ar","16000","-b:a","64k",str(audio)])
                        duration=float(run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(vpath)]))
                        st.write("Groq Whisper ဖြင့် transcript ထုတ်နေသည်…")
                        g=Groq(api_key=groq_key)
                        with audio.open("rb") as f:
                            tr=g.audio.transcriptions.create(file=(audio.name,f.read()),model="whisper-large-v3-turbo",response_format="text")
                        transcript=str(tr)
                        st.write(f"Gemini {model} ဖြင့် မြန်မာ recap ရေးနေသည်…")
                        client=genai.Client(api_key=gemini_key)
                        prompt=("သင်သည် မြန်မာ Movie Recap ဇာတ်ညွှန်းရေးသူဖြစ်သည်။ အောက်ပါ transcript ကို အခြေခံပြီး "
                                "ဇာတ်လမ်းအစဉ်လိုက်၊ သဘာဝကျသော မြန်မာစကားပြောဟန်ဖြင့် recap ရေးပါ။ မရှိသောအချက်အလက် မတီထွင်ပါနှင့်။ "
                                "အရေးကြီးဇာတ်ကွက်များကို ထိန်းထားပြီး voice-over ဖတ်ရန် အဆင်ပြေအောင် စာပိုဒ်တိုများရေးပါ။\n\nTRANSCRIPT:\n"+transcript[:90000])
                        response=client.models.generate_content(model=model,contents=prompt)
                        recap=response.text or ""
                        srt=make_srt(recap,duration)
                        st.session_state["last_transcript"]=transcript
                        st.session_state["last_recap"]=recap
                        st.session_state["last_srt"]=srt
                        status.update(label="Recap assets ready",state="complete")
                st.success("Recap script နဲ့ SRT ထုတ်ပြီးပါပြီ။")
            except Exception as e:
                st.error(f"Processing failed: {e}")

if "last_recap" in st.session_state:
    st.markdown("---")
    st.markdown("### 2. Edit recap script")
    recap=st.text_area("မြန်မာ Recap Script",value=st.session_state["last_recap"],height=260)
    st.session_state["last_recap"]=recap
    c1,c2=st.columns(2)
    c1.download_button("⬇️ Download Transcript",st.session_state["last_transcript"],file_name="transcript.txt",mime="text/plain",use_container_width=True)
    c2.download_button("⬇️ Download Recap Script",recap,file_name="burmese_recap.txt",mime="text/plain",use_container_width=True)
    st.markdown("### 3. Subtitle (SRT)")
    if st.button("Refresh SRT from edited script"):
        st.session_state["last_srt"]=make_srt(recap,1800)
    srt=st.text_area("SRT content",value=st.session_state["last_srt"],height=220)
    st.session_state["last_srt"]=srt
    st.download_button("⬇️ Download SRT",srt,file_name="burmese_recap.srt",mime="application/x-subrip",use_container_width=True)
    st.markdown("### 4. Render final MP4 with uploaded narration")
    narration=st.file_uploader("Narration audio (MP3/WAV/M4A)",type=["mp3","wav","m4a","aac"],key="narration")
    use_subs=st.checkbox("Burn SRT subtitles into video",value=True)
    if video and narration and st.button("🎞️ Render Final MP4",type="primary",use_container_width=True):
        try:
            with tempfile.TemporaryDirectory(prefix="render_") as td:
                root=Path(td); vp=root/("input"+Path(video.name).suffix.lower()); vp.write_bytes(video.getvalue())
                ap=root/("voice"+Path(narration.name).suffix.lower()); ap.write_bytes(narration.getvalue())
                sp=root/"subs.srt"; sp.write_text(srt,encoding="utf-8")
                out=root/"final.mp4"
                cmd=["ffmpeg","-y","-i",str(vp),"-i",str(ap)]
                if use_subs:
                    subpath=str(sp).replace("\\","/").replace(":","\\:")
                    cmd += ["-vf",f"subtitles='{subpath}'"]
                cmd += ["-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-shortest","-movflags","+faststart",str(out)]
                with st.spinner("Final MP4 render လုပ်နေသည်…"):
                    run(cmd)
                st.success("Final MP4 ပြီးပါပြီ။")
                st.video(out.read_bytes())
                st.download_button("⬇️ Download Final MP4",out.read_bytes(),file_name="burmese_recap.mp4",mime="video/mp4",use_container_width=True)
        except Exception as e:
            st.error(f"Render failed: {e}")

st.caption("Tip: Render free-host limits ရှိနိုင်ပါတယ်။ အရင်ဆုံး 1–2 မိနစ် video နဲ့စမ်းပါ။")
