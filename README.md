# ONE CLICK — ONE MOVIE RECAP

A Streamlit-based Burmese movie recap workflow using FFmpeg, Groq Whisper, Google Gemini, and Edge TTS.

## Features
- Video metadata inspection and FFmpeg audio extraction
- Groq Whisper transcription
- Gemini model discovery and Burmese recap generation in transcript chunks
- Editable recap script and SRT draft
- Myanmar Edge TTS voice generation or uploaded narration
- Video preview, subtitle/text/blur/logo settings, confirmation before rendering
- SQLite project metadata and project assets on the configured data directory
- Render Docker deployment with FFmpeg and Noto fonts

## Local setup
1. Install Python 3.11 and FFmpeg/FFprobe.
2. `python -m venv .venv` and activate it.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and set keys in your shell (the app does not automatically load `.env`).
5. `streamlit run app.py`

After entering the app password, open Settings and enter Groq and Gemini keys. For durable encrypted key storage, set `APP_ENCRYPTION_KEY` in the host environment using a fresh Fernet key generated with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep this key private and stable: changing it prevents previously encrypted keys from being decrypted. You can alternatively set `GROQ_API_KEY` and `GEMINI_API_KEY` as server environment variables. Without `APP_ENCRYPTION_KEY`, the Settings form only holds keys for the active session.

## Render
Create a new Web Service from this repository and use Docker. Configure `APP_ACCESS_PASSWORD` (required to unlock the app), `APP_ENCRYPTION_KEY` (for encrypted saved API keys), `GROQ_API_KEY`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL`. The included `render.yaml` uses a Starter web service plus a persistent disk, which is a paid configuration and can incur charges. Do not deploy it without checking current Render pricing. A free service without persistent disk would lose local project assets across restarts/redeploys. Large full-length movies may exceed processing time, memory, or CPU limits; start with a short test clip.

## Workflow
Create Recap → upload movie → ONE CLICK RECAP → edit SRT / script → Voice Studio → Video Editor → DONE — CONFIRM EDITS → RENDER FINAL MP4.

## Important limitations
- Gemini model selection checks the model list accessible to the configured API key; if the requested model is unavailable, the app selects a listed non-preview Flash model. Availability and quotas depend on Google account access.
- Subtitle timing generated from the recap is a draft estimate, not forced alignment. Review before export.
- Edge TTS voice availability can change. If Myanmar voices are unavailable, upload a narration file instead.
- The current build does not include login/multi-user isolation. Encrypted saved API keys are app-wide for this single-user/private deployment, not separate per user. Do not expose a shared public instance with private credentials.
- Video editor preview currently previews the source video; overlay and render settings are applied by FFmpeg at export. Validate a short test clip first.

## Direct video URL input

The Create Recap page supports both local uploads and **direct downloadable video-file URLs**. Supported extensions: MP4, MOV, MKV, WEBM, AVI, and M4V. Remote downloads are capped at 200 MB and use HTTP(S) only. Streaming pages such as YouTube/Netflix, playlists, private-network URLs, and non-video webpages are not supported. Use only videos you own or have permission to process.

## Free Render deployment notes

The included `render.yaml` targets the Render Free web service and stores app data under `/tmp/recap-data`. Free Render services have an ephemeral filesystem: uploaded movies, recap projects, SQLite records, and saved settings may disappear after restarts, spin-downs, or redeploys. Free instances also have limited CPU/RAM and may not finish long-video processing. Use short clips for testing, and keep copies of any generated files you need. Persistent project storage requires a paid persistent disk or a separate storage/database design.

Required Render environment variables:
- `APP_ACCESS_PASSWORD`: a strong private password to unlock the app.
- `GROQ_API_KEY` and `GEMINI_API_KEY`: provider API keys.
- `APP_ENCRYPTION_KEY`: optional but recommended for encrypted saved provider keys; generate a Fernet key and keep it private.
- `GEMINI_MODEL`: optional preferred Gemini model.

After a push to the connected GitHub branch, wait for Render's deploy to finish, then open the service URL and test with a short, authorized direct MP4 URL. If Render is not linked to this repository/branch, use the Render Dashboard to trigger a deploy manually.
