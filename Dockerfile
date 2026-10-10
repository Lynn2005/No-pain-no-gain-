FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_DATA_DIR=/var/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-extra && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY . .
RUN mkdir -p /var/data
EXPOSE 10000
CMD ["sh","-c","gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --timeout 900 app:app"]
