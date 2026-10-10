# ONE CLICK — ONE MOVIE RECAP

A Burmese movie recap web app built with Flask using FFmpeg, Groq Whisper, Google Gemini, and Edge TTS.

## Features
- Video upload or direct video-file URL input
- FFmpeg audio extraction and Groq Whisper transcription
- Gemini Burmese recap generation, with Groq fallback
- Editable recap script and regenerated SRT draft
- Myanmar Edge TTS narration generation
- Final MP4 rendering with narration/original audio selection and subtitles
- SQLite project metadata and project assets on the configured data directory
- Mobile-friendly Flask interface and Render Docker deployment

## Local setup
1. Install Python 3.11 and FFmpeg/FFprobe.
2. `python -m venv .venv` and activate it.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and set keys in your shell (the app does not automatically load `.env`).
5. `python app.py` or `gunicorn --bind 0.0.0.0:10000 app:app`

Set `GROQ_API_KEY` and `GEMINI_API_KEY` in Render Environment Variables, or enter them in the create form for that request only. Keys entered in the form are not stored in the database.

## Render
Create a Docker Web Service from this repository. Set `GROQ_API_KEY`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL` in Render Environment Variables. The included `render.yaml` uses the Free plan and temporary storage. Large full-length movies may exceed processing time, memory, or CPU limits; start with a short test clip.

## Workflow
Open the home page → upload a video or enter a direct video URL → generate recap → edit the Burmese script → generate narration → render and download the final MP4.

## Important limitations
- Gemini model selection checks the model list accessible to the configured API key; if the requested model is unavailable, the app selects a listed non-preview Flash model. Availability and quotas depend on Google account access.
- Subtitle timing generated from the recap is a draft estimate, not forced alignment. Review before export.
- Edge TTS voice availability can change. If Myanmar voices are unavailable, upload a narration file instead.
- The current build does not include login/multi-user isolation. Anyone who can access the public URL can submit jobs and consume server resources. Do not use a shared public instance for private data.
- The editor currently provides basic audio selection and subtitle controls; text/logo/blur visual editing from the former interface is not included in this initial Flask version. Validate a short test clip first.

## Direct video URL input

The home page supports both local uploads and **direct downloadable video-file URLs**. Supported extensions: MP4, MOV, MKV, WEBM, AVI, and M4V. Remote downloads are capped at 200 MB and use HTTP(S) only. Streaming pages such as YouTube/Netflix, playlists, private-network URLs, and non-video webpages are not supported. Use only videos you own or have permission to process.

## Free Render deployment notes

The included `render.yaml` targets the Render Free web service and stores app data under `/tmp/recap-data`. Free Render services have an ephemeral filesystem: uploaded movies, recap projects, SQLite records, and saved settings may disappear after restarts, spin-downs, or redeploys. Free instances also have limited CPU/RAM and may not finish long-video processing. Use short clips for testing, and keep copies of any generated files you need. Persistent project storage requires a paid persistent disk or a separate storage/database design.

Required Render environment variables:
- `GROQ_API_KEY` and `GEMINI_API_KEY`: provider API keys.
- `GEMINI_MODEL`: optional preferred Gemini model.

After a push to the connected GitHub branch, wait for Render's deploy to finish, then open the service URL and test with a short direct MP4 URL. If Render is not linked to this repository/branch, use the Render Dashboard to trigger a deploy manually.
