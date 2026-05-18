FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus \
    dbus-daemon \
    libdbus-1-3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8111 \
    HEADLESS=true \
    STATE_PATH=/app/storage/linkedin_state.json \
    CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000 \
    PLAYWRIGHT_CHROMIUM_LAUNCH_ARGS="--disable-gpu --disable-web-resources --disable-dev-shm-usage --no-sandbox --disable-setuid-sandbox --disable-infobars --disable-crash-reporter" \
    PLAYWRIGHT_DOWNLOAD_HOST=https://github.com

WORKDIR /app

COPY requirements.txt ./

RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/storage/session /app/storage/runtime /app/data/raw /app/data/output /tmp \
    && chown -R pwuser:pwuser /app /tmp

USER pwuser

EXPOSE 8111

VOLUME ["/app/storage", "/app/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; import os; port = os.getenv('PORT', '8111'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health').read()"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8111}"]
