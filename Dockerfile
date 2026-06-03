FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# SQLite DB (subs + curated picks) lives on the mounted /data volume.
ENV DB_PATH=/data/curatarr.db

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
