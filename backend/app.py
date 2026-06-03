"""FastAPI app: sync a big M3U from a saved URL, browse it Jellyseerr-style,
import picks into a curated M3U.

Config via environment (all optional):
  SOURCE_M3U    initial source (URL or local path) seeded on first run
  SOURCE_CACHE  where a downloaded M3U is cached (default ./source_cache.m3u)
  DEST_M3U      curated output M3U (default ./curated.m3u)
  DB_PATH       SQLite cache + import ledger (default ./curator.db)

The active source URL is stored in the DB (meta.source_url) and editable at
runtime via the UI / API, so nothing is hardcoded.
"""
from __future__ import annotations

import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import parser
from .db import DB
from .importer import import_rows

SOURCE_M3U = os.environ.get("SOURCE_M3U", "")
SOURCE_CACHE = os.environ.get("SOURCE_CACHE", "source_cache.m3u")
DEST_M3U = os.environ.get("DEST_M3U", "curated.m3u")
DB_PATH = os.environ.get("DB_PATH", "curator.db")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="M3U Curator")
db = DB(DB_PATH)

# Seed the saved source from the env default on first run only.
if db.get_meta("source_url") is None and SOURCE_M3U:
    db.set_meta("source_url", SOURCE_M3U)


# --- helpers --------------------------------------------------------------
def _is_remote(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


def redact(src: str) -> str:
    """Mask credentials for safe logging/echo: both user:pass@host and
    Xtream-style ?username=…&password=… query params."""
    if not src:
        return src
    src = re.sub(r"://[^/@]+@", "://***@", src)
    src = re.sub(r"(?i)\b(username|password|user|pass|token)=[^&\s]+", r"\1=***", src)
    return src


def _download(url: str, dest: str) -> int:
    """Stream a URL to dest (constant memory). Returns byte size. Error messages
    are redacted so provider credentials never surface in responses or logs."""
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "m3u-curator/1"})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out, length=256 * 1024)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(502, f"Download failed: {redact(str(e))}")
    os.replace(tmp, dest)
    return os.path.getsize(dest)


# --- models ---------------------------------------------------------------
class SourceRequest(BaseModel):
    url: str


class ImportRequest(BaseModel):
    ids: list[int] | None = None
    series_key: str | None = None
    season: int | None = None  # only with series_key; None = whole series


# --- source + sync --------------------------------------------------------
@app.get("/api/status")
def status():
    return {
        "source_url": db.get_meta("source_url") or "",
        "dest": DEST_M3U,
        "last_sync": db.get_meta("last_sync"),
        "last_bytes": db.get_meta("last_bytes"),
        "counts": db.counts(),
    }


@app.get("/api/source")
def get_source():
    return {
        "url": db.get_meta("source_url") or "",
        "last_sync": db.get_meta("last_sync"),
        "last_bytes": db.get_meta("last_bytes"),
    }


@app.post("/api/source")
def set_source(req: SourceRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "URL is required")
    if not (_is_remote(url) or os.path.exists(url)):
        raise HTTPException(400, "Must be an http(s):// URL or an existing local path")
    db.set_meta("source_url", url)
    return {"url": url}


@app.post("/api/sync")
def sync():
    """Fetch fresh data from the saved source and rebuild the index."""
    src = db.get_meta("source_url")
    if not src:
        raise HTTPException(400, "No source configured — set a URL first")

    if _is_remote(src):
        size = _download(src, SOURCE_CACHE)
        path = SOURCE_CACHE
    else:
        if not os.path.exists(src):
            raise HTTPException(404, f"Source not found: {src}")
        path = src
        size = os.path.getsize(src)

    count = db.replace_items(parser.parse(path))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.set_meta("last_sync", now)
    db.set_meta("last_bytes", str(size))
    return {"parsed": count, "bytes": size, "counts": db.counts()}


# --- browse ---------------------------------------------------------------
@app.get("/api/groups")
def groups(kind: str = Query(..., pattern="^(live|movie|series)$")):
    return {"groups": db.groups(kind)}


@app.get("/api/items")
def items(
    kind: str = Query(..., pattern="^(live|movie)$"),
    group: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=500),
):
    offset = (max(page, 1) - 1) * page_size
    rows, total = db.items(kind, group, q, page_size, offset)
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@app.get("/api/series")
def series(
    group: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=500),
):
    offset = (max(page, 1) - 1) * page_size
    rows, total = db.series_list(group, q, page_size, offset)
    return {"series": rows, "total": total, "page": page, "page_size": page_size}


@app.get("/api/series/detail")
def series_detail(series_key: str):
    return db.series_detail(series_key)


# --- import ---------------------------------------------------------------
@app.post("/api/import")
def do_import(req: ImportRequest):
    if req.series_key:
        rows = db.rows_for_series(req.series_key, req.season)
    elif req.ids:
        rows = db.rows_for_ids(req.ids)
    else:
        raise HTTPException(400, "Provide ids or series_key")
    if not rows:
        raise HTTPException(404, "Nothing matched")
    return import_rows(db, DEST_M3U, rows)


@app.get("/api/imported")
def imported():
    return {"imported": db.imported_list()}


# --- static frontend ------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")
