FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# Data lives on a mounted volume; override these to point at real paths.
ENV SOURCE_M3U=/data/source.m3u \
    DEST_M3U=/data/curated.m3u \
    DB_PATH=/data/curator.db

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
