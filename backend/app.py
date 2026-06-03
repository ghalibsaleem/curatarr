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

import json
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
from .xtream_client import ProviderXC, creds_from_source, stream_hash, stream_url

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


# --- subscriptions (upstream) ----------------------------------------------
def _base_user() -> str:
    return db.get_meta("xc_user") or "curator"


def _next_out_user(used: set[str]) -> str:
    """Lowest free output username: curator, curator2, curator3, …"""
    bu = _base_user()
    i = 1
    while True:
        name = bu if i == 1 else f"{bu}{i}"
        if name not in used:
            return name
        i += 1


def get_subs() -> list[dict]:
    """List of provider subscriptions [{base,user,pass,out_user}]. out_user is a
    STABLE per-sub Xtream output username so removing one sub never renames the
    others. Migrates a legacy single source and older subs without out_user."""
    raw = db.get_meta("subs")
    if raw:
        subs = json.loads(raw)
    else:
        base, user, pwd = db.get_meta("src_base"), db.get_meta("src_user"), db.get_meta("src_pass")
        subs = [{"base": base, "user": user, "pass": pwd}] if (base and user and pwd) else []
    changed = not raw and bool(subs)
    used = {s["out_user"] for s in subs if s.get("out_user")}
    for s in subs:
        if not s.get("out_user"):
            s["out_user"] = _next_out_user(used)
            used.add(s["out_user"])
            changed = True
    if changed:
        db.set_meta("subs", json.dumps(subs))
    return subs


def set_subs(new_list: list[dict]) -> list[dict]:
    """Persist subs from {base,user,pass} input, preserving each existing sub's
    stable out_user (matched by credentials) and assigning fresh names to new
    ones — so add/remove/reorder never disturbs other accounts."""
    by_key = {(s["base"], s["user"], s["pass"]): s["out_user"] for s in get_subs()}
    used = set(by_key.values())
    result = []
    for s in new_list:
        key = (s["base"], s["user"], s["pass"])
        ou = by_key.get(key)
        if not ou or ou in {r["out_user"] for r in result}:
            ou = _next_out_user(used | {r["out_user"] for r in result})
        result.append({"base": s["base"], "user": s["user"], "pass": s["pass"], "out_user": ou})
    db.set_meta("subs", json.dumps(result))
    return result


# --- output accounts (what Dispatcharr connects to) ------------------------
def out_accounts() -> list[dict]:
    """One Xtream output account per subscription, using the sub's stable
    out_user. All share one password. Each maps to the sub whose credentials its
    stream redirects use."""
    pwd = db.get_meta("xc_pass") or ""
    return [{"username": s["out_user"], "password": pwd, "sub": s} for s in get_subs()]


def _sub_for_user(username: str) -> dict | None:
    for a in out_accounts():
        if a["username"] == username:
            return a["sub"]
    return None


def _check_creds(username: str, password: str) -> bool:
    pwd = db.get_meta("xc_pass") or ""
    users = {a["username"] for a in out_accounts()}
    return username in users and secrets.compare_digest(password, pwd)


# --- models ---------------------------------------------------------------
class Sub(BaseModel):
    url: str
    username: str
    password: str


class SourceRequest(BaseModel):
    subs: list[Sub]


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
    subs = get_subs()
    return {
        "source_url": subs[0]["base"] if subs else "",
        "sub_count": len(subs),
        "last_sync": db.get_meta("last_sync"),
        "counts": db.counts(),
    }


@app.get("/api/source")
def get_source():
    return {
        "subs": [
            {"url": s["base"], "username": s["user"], "password": s["pass"]}
            for s in get_subs()
        ],
        "last_sync": db.get_meta("last_sync"),
    }


@app.post("/api/source")
def set_source(req: SourceRequest):
    if not req.subs:
        raise HTTPException(400, "At least one subscription is required")
    subs = []
    for s in req.subs:
        url = (s.url or "").strip()
        user = (s.username or "").strip()
        pwd = (s.password or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "Each Server URL must start with http:// or https://")
        if not (user and pwd):
            raise HTTPException(400, "Each subscription needs a username and password")
        parts = urllib.parse.urlsplit(url)
        subs.append({"base": f"{parts.scheme}://{parts.netloc}", "user": user, "pass": pwd})
    set_subs(subs)
    return {"subs": [{"url": s["base"], "username": s["user"]} for s in subs]}


def _provider() -> ProviderXC:
    """Provider client for ingest — reads from the primary subscription (subs[0]).
    Both subs are the same panel, so one read covers the shared catalogue."""
    subs = get_subs()
    if not subs:
        raise HTTPException(400, "Configure at least one provider subscription first")
    s = subs[0]
    return ProviderXC(s["base"], s["user"], s["pass"])


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
            pid = str(s.get("stream_id"))
            url = xc.live_url(pid)
            rows.append({
                "kind": "live", "name": s.get("name", ""),
                "group_title": live_cat.get(str(s.get("category_id")), ""),
                "tvg_logo": s.get("stream_icon") or "", "url": url,
                "series_key": "", "series_name": "", "season": None,
                "episode": None, "tmdb": "", "provider_id": pid,
                "hash": stream_hash(url),
            })
        # Movies must be fetched per category (provider returns nothing for
        # get_vod_streams without a category_id).
        for cid, cname in vod_cat.items():
            for s in xc.vod_streams(cid):
                pid = str(s.get("stream_id"))
                url = xc.movie_url(pid, s.get("container_extension") or "mp4")
                rows.append({
                    "kind": "movie", "name": s.get("name", ""),
                    "group_title": cname, "tvg_logo": s.get("stream_icon") or "",
                    "url": url, "series_key": "", "series_name": "",
                    "season": None, "episode": None,
                    "tmdb": str(s.get("tmdb") or ""), "provider_id": pid,
                    "hash": stream_hash(url),
                })
        for s in xc.series():
            sid = str(s.get("series_id"))
            rows.append({
                "kind": "series", "name": s.get("name", ""),
                "group_title": ser_cat.get(str(s.get("category_id")), ""),
                "tvg_logo": s.get("cover") or "", "url": "",
                "series_key": sid, "series_name": s.get("name", ""),
                "season": None, "episode": None,
                "tmdb": str(s.get("tmdb") or ""), "provider_id": "",
                "hash": f"series:{sid}",
            })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Xtream fetch failed: {e}")

    count = db.replace_items_rows(rows)
    db.backfill_from_items()  # give existing picks their TMDB id + provider_id
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
                "provider_id": eid,
            })
    return out


class CountsRequest(BaseModel):
    series_keys: list[str]


@app.post("/api/series/counts")
def series_counts(req: CountsRequest):
    """Total season/episode counts for the given series. Cached in the scan
    cache; uncached ones are fetched from the provider (concurrently, bounded)
    and stored, so each series costs one provider call at most once."""
    keys = list(dict.fromkeys(req.series_keys))[:60]  # de-dupe, cap per request
    out = db.cached_series_totals(keys)
    missing = [k for k in keys if k not in out]
    if missing:
        xc = _provider()

        def fetch(k):
            try:
                info = xc.series_info(k)
                eps = info.get("episodes") or {}
                seasons = len(eps)
                episodes = sum(len(v) for v in eps.values())
                return k, seasons, episodes
            except Exception:
                return k, None, None

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            for k, seasons, episodes in ex.map(fetch, missing):
                if seasons is not None:
                    db.set_series_total(k, seasons, episodes)
                    out[k] = {"seasons": seasons, "episodes": episodes}
    return {"counts": out}


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
    """Connection details to paste into Dispatcharr — one Xtream account per
    subscription, so Dispatcharr load-balances across them."""
    base = str(request.base_url).rstrip("/")
    accounts = [{
        "username": a["username"],
        "password": a["password"],
        "player_api": f"{base}/player_api.php?username={a['username']}&password={a['password']}",
    } for a in out_accounts()]
    return {
        "server_url": base,
        "accounts": accounts,
        "note": "Add EACH account in Dispatcharr as an Xtream Codes account "
                "(VOD scanning ON, max connections 1). Dispatcharr will balance "
                "streams across them.",
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
    sub = _sub_for_user(username)
    stream_id = filename.rsplit(".", 1)[0]
    ref = db.led_stream_ref(stream_id)
    if not ref:
        raise HTTPException(404, "Unknown stream")
    if sub and ref["provider_id"]:
        # Rebuild against the subscription this output account maps to — this is
        # what lets Dispatcharr load-balance the same pick across both subs.
        url = stream_url(sub["base"], sub["user"], sub["pass"],
                         ref["kind"], ref["provider_id"], ref["container_extension"])
    else:
        # Fallback for picks imported before provider_id existed.
        url = db.led_url(int(stream_id)) if str(stream_id).isdigit() else None
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
