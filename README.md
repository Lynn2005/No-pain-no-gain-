# One-Click AI Movie Recap

A separate Streamlit project for making Burmese movie recap assets and an MP4 preview/render. This repository is independent from Yoon Recap.

## Render deployment

1. Open Render and select **New + → Blueprint**.
2. Connect `Lynn2005/No-pain-no-gain-` and deploy the `render.yaml` Blueprint.
3. In the Render service's Environment settings, set `GROQ_API_KEY` and `GEMINI_API_KEY`.
4. Redeploy, then test with a short video first.

Never put real API keys in GitHub. Streamlit Community Cloud and free Render services can have memory, disk, request-duration and upload limits; long movies may fail or take a long time.

## Local setup

Requirements: Python 3.11+, FFmpeg and ffprobe installed and on PATH.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Set `GROQ_API_KEY` and `GEMINI_API_KEY` as environment variables, or enter them in the app for the current session.

## Features

- Upload a video and extract its audio using FFmpeg
- Transcribe audio using Groq Whisper
- Generate a Burmese recap script using Gemini
- Edit the recap and generate a downloadable SRT file
- Upload narration audio and render a subtitle MP4

This is an initial implementation, not an end-to-end production acceptance test. Provider quotas, Burmese TTS availability, Myanmar font rendering, and FFmpeg behavior vary by host. Always validate output with a short sample before processing a full movie.