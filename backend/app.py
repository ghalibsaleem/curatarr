"""FastAPI app: read a provider's Xtream catalogue, browse it Jellyseerr-style,
cherry-pick, and re-serve the picks as a curated Xtream panel for Dispatcharr.

Config via environment (all optional):
  SOURCE_M3U    initial source Xtream URL (get.php/player_api) seeded on first run
  DB_PATH       SQLite cache + import ledger (default ./curator.db)
  XC_USER/XC_PASS  credentials for our Xtream wrapper (generated if unset)

The active source URL is stored in the DB (meta.source_url) and editable at
runtime via the UI / API, so nothing is hardcoded.
"""
from __future__ import annotations

import os
import secrets
import urllib.parse
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import xtream
from .db import DB
from .importer import import_rows
from .xtream_client import ProviderXC, creds_from_source, stream_hash

SOURCE_M3U = os.environ.get("SOURCE_M3U", "")
DB_PATH = os.environ.get("DB_PATH", "curator.db")
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI(title="M3U Curator")
db = DB(DB_PATH)

# Provider source is stored as discrete fields (src_base/src_user/src_pass).
# Seed them on first run from an existing combined source_url (older builds) or
# from the SOURCE_M3U env (a get.php/player_api link).
if db.get_meta("src_base") is None:
    seed = db.get_meta("source_url") or SOURCE_M3U
    if seed:
        base, user, pwd = creds_from_source(seed)
        if base:
            db.set_meta("src_base", base)
        if user:
            db.set_meta("src_user", user)
        if pwd:
            db.set_meta("src_pass", pwd)

# Xtream wrapper credentials — generated once, shown in the UI to paste into
# Dispatcharr's XC account. Overridable via env on first run.
if db.get_meta("xc_user") is None:
    db.set_meta("xc_user", os.environ.get("XC_USER", "curator"))
if db.get_meta("xc_pass") is None:
    db.set_meta("xc_pass", os.environ.get("XC_PASS", secrets.token_hex(8)))


def _xc_creds() -> tuple[str, str]:
    return db.get_meta("xc_user") or "", db.get_meta("xc_pass") or ""


def _check_creds(username: str, password: str) -> bool:
    u, p = _xc_creds()
    return secrets.compare_digest(username, u) and secrets.compare_digest(password, p)


# --- models ---------------------------------------------------------------
class SourceRequest(BaseModel):
    url: str
    username: str
    password: str


class ImportRequest(BaseModel):
    ids: list[int] | None = None              # live/movie scan-cache ids
    series_key: str | None = None             # provider series id
    season: int | None = None                 # with series_key: just this season
    episode_ids: list[str] | None = None      # with series_key: specific episodes


class UnimportRequest(BaseModel):
    ids: list[int]  # ledger row ids (from /api/imported)


# --- source + sync --------------------------------------------------------
@app.get("/api/status")
def status():
    return {
        "source_url": db.get_meta("src_base") or "",
        "source_user": db.get_meta("src_user") or "",
        "last_sync": db.get_meta("last_sync"),
        "counts": db.counts(),
    }


@app.get("/api/source")
def get_source():
    return {
        "url": db.get_meta("src_base") or "",
        "username": db.get_meta("src_user") or "",
        "password": db.get_meta("src_pass") or "",
        "last_sync": db.get_meta("last_sync"),
    }


@app.post("/api/source")
def set_source(req: SourceRequest):
    url = req.url.strip()
    user = req.username.strip()
    pwd = req.password.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Server URL must start with http:// or https://")
    if not (user and pwd):
        raise HTTPException(400, "Username and password are required")
    # Normalise to scheme://host[:port] (strip any pasted /get.php path or query).
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    db.set_meta("src_base", base)
    db.set_meta("src_user", user)
    db.set_meta("src_pass", pwd)
    return {"url": base, "username": user}


def _provider() -> ProviderXC:
    base = db.get_meta("src_base")
    user = db.get_meta("src_user")
    pwd = db.get_meta("src_pass")
    if not (base and user and pwd):
        raise HTTPException(400, "Configure the provider Server URL, username and "
                                 "password first")
    return ProviderXC(base, user, pwd)


@app.post("/api/sync")
def sync():
    """Pull categories/movies/series from the provider's Xtream API and rebuild
    the browse index. Episodes are fetched lazily (on open/import), so series
    contribute one row each here rather than thousands of episodes."""
    xc = _provider()
    try:
        xc.authenticate()
    except Exception as e:
        raise HTTPException(502, f"Xtream auth failed: {e}")

    def catmap(cats):
        return {str(c.get("category_id")): c.get("category_name", "") for c in cats}

    try:
        live_cat = catmap(xc.live_categories())
        vod_cat = catmap(xc.vod_categories())
        ser_cat = catmap(xc.series_categories())

        rows: list[dict] = []
        for s in xc.live_streams():
            url = xc.live_url(s.get("stream_id"))
            rows.append({
                "kind": "live", "name": s.get("name", ""),
                "group_title": live_cat.get(str(s.get("category_id")), ""),
                "tvg_logo": s.get("stream_icon") or "", "url": url,
                "series_key": "", "series_name": "", "season": None,
                "episode": None, "tmdb": "", "hash": stream_hash(url),
            })
        # Movies must be fetched per category (provider returns nothing for
        # get_vod_streams without a category_id).
        for cid, cname in vod_cat.items():
            for s in xc.vod_streams(cid):
                url = xc.movie_url(s.get("stream_id"), s.get("container_extension") or "mp4")
                rows.append({
                    "kind": "movie", "name": s.get("name", ""),
                    "group_title": cname, "tvg_logo": s.get("stream_icon") or "",
                    "url": url, "series_key": "", "series_name": "",
                    "season": None, "episode": None,
                    "tmdb": str(s.get("tmdb") or ""), "hash": stream_hash(url),
                })
        for s in xc.series():
            sid = str(s.get("series_id"))
            rows.append({
                "kind": "series", "name": s.get("name", ""),
                "group_title": ser_cat.get(str(s.get("category_id")), ""),
                "tvg_logo": s.get("cover") or "", "url": "",
                "series_key": sid, "series_name": s.get("name", ""),
                "season": None, "episode": None,
                "tmdb": str(s.get("tmdb") or ""), "hash": f"series:{sid}",
            })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Xtream fetch failed: {e}")

    count = db.replace_items_rows(rows)
    db.backfill_ledger_tmdb()  # give already-imported picks their TMDB ids
    db.set_meta("last_sync", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"parsed": count, "counts": db.counts()}


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


def _series_episode_rows(series_key: str) -> list[dict]:
    """Fetch a series' episodes from the provider (lazy) as ledger-shaped dicts.
    Each row carries a stable hash, the provider stream URL, and season/episode."""
    xc = _provider()
    meta = db.series_row(series_key)
    name = meta["name"] if meta else ""
    group = meta["group_title"] if meta else ""
    tmdb = meta["tmdb"] if meta else ""
    try:
        info = xc.series_info(series_key)
    except Exception as e:
        raise HTTPException(502, f"Xtream series fetch failed: {e}")
    out: list[dict] = []
    for snum, eps in (info.get("episodes") or {}).items():
        season = int(snum) if str(snum).isdigit() else 0
        for e in eps:
            eid = str(e.get("id"))
            url = xc.episode_url(eid, e.get("container_extension") or "mp4")
            out.append({
                "ep_id": eid, "hash": stream_hash(url), "kind": "series",
                "name": e.get("title") or name, "group_title": group,
                "url": url, "extinf": "", "series_key": series_key,
                "series_name": name, "season": season,
                "episode": e.get("episode_num"), "tmdb": tmdb,
            })
    return out


@app.get("/api/series/detail")
def series_detail(series_key: str):
    rows = _series_episode_rows(series_key)
    imported = db.is_imported(r["hash"] for r in rows)
    seasons: dict[int, list] = {}
    for r in rows:
        seasons.setdefault(r["season"], []).append({
            "ep_id": r["ep_id"], "name": r["name"], "season": r["season"],
            "episode": r["episode"], "imported": r["hash"] in imported,
        })
    return {
        "series_key": series_key,
        "seasons": [{"season": s, "episodes": eps} for s, eps in sorted(seasons.items())],
    }


# --- import ---------------------------------------------------------------
@app.post("/api/import")
def do_import(req: ImportRequest):
    if req.series_key:
        rows = _series_episode_rows(req.series_key)
        if req.season is not None:
            rows = [r for r in rows if r["season"] == req.season]
        if req.episode_ids:
            wanted = set(map(str, req.episode_ids))
            rows = [r for r in rows if r["ep_id"] in wanted]
    elif req.ids:
        rows = db.rows_for_ids(req.ids)
    else:
        raise HTTPException(400, "Provide ids or series_key")
    if not rows:
        raise HTTPException(404, "Nothing matched")
    return import_rows(db, rows)


@app.get("/api/imported")
def imported():
    return {"imported": db.imported_list()}


@app.post("/api/unimport")
def unimport(req: UnimportRequest):
    removed = db.remove_imported(req.ids)
    return {"removed": removed}


@app.get("/api/xc-info")
def xc_info(request: Request):
    """Connection details to paste into Dispatcharr as an Xtream Codes account."""
    user, pwd = _xc_creds()
    base = str(request.base_url).rstrip("/")
    return {
        "server_url": base,
        "username": user,
        "password": pwd,
        "player_api": f"{base}/player_api.php?username={user}&password={pwd}",
        "note": "Add in Dispatcharr as an Xtream Codes account with VOD scanning ON.",
    }


# --- Xtream Codes wrapper (consumed by Dispatcharr) -----------------------
@app.get("/player_api.php")
@app.get("/panel_api.php")
def player_api(request: Request):
    p = dict(request.query_params)
    if not _check_creds(p.get("username", ""), p.get("password", "")):
        # Mirror an XC auth failure: present user_info with auth=0.
        return JSONResponse({"user_info": {"auth": 0}, "server_info": {}})
    base = str(request.base_url).rstrip("/")
    body = xtream.dispatch(db, p.get("action"), p, base)
    return JSONResponse(body)


@app.get("/xmltv.php")
def xmltv(request: Request):
    # We don't provide EPG; return a valid empty TV document.
    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?>\n<tv></tv>',
        media_type="application/xml",
    )


def _stream_redirect(username: str, password: str, filename: str):
    if not _check_creds(username, password):
        raise HTTPException(403, "Forbidden")
    stream_id = filename.rsplit(".", 1)[0]
    url = xtream.resolve_redirect(db, stream_id)
    if not url:
        raise HTTPException(404, "Unknown stream")
    # Dispatcharr's VOD proxy follows this to the provider (we stay out of the
    # byte path); credentials in the target URL are the provider's own.
    return RedirectResponse(url, status_code=302)


@app.get("/movie/{username}/{password}/{filename}")
def stream_movie(username: str, password: str, filename: str):
    return _stream_redirect(username, password, filename)


@app.get("/series/{username}/{password}/{filename}")
def stream_series(username: str, password: str, filename: str):
    return _stream_redirect(username, password, filename)


@app.get("/live/{username}/{password}/{filename}")
def stream_live(username: str, password: str, filename: str):
    return _stream_redirect(username, password, filename)


# --- static frontend ------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")
