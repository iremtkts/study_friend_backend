FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd --system app \
    && useradd --system --gid app --create-home app \
    && mkdir -p /app/uploads \
    && chown -R app:app /app

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["CMD-SHELL", "python -c 'import os, urllib.request; urllib.request.urlopen(\"http://127.0.0.1:\" + os.getenv(\"PORT\", \"8000\") + \"/health\", timeout=3)'"]

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
