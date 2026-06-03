"""Curated Xtream Codes wrapper.

Serves the import ledger as an Xtream Codes panel so Dispatcharr can ingest it as
an XC account (VOD scanning on) and thus expose ONLY the hand-picked movies,
series and live channels — with native VOD metadata and VOD2MLIB support.

Design notes (verified against Dispatcharr v0.25.1 source):
  * get_series_info returns episodes as a SEASON-KEYED OBJECT ({"1":[...]}),
    not an array — Dispatcharr iterates `episodes.items()` and uses each
    episode's `id` as the stream id (apps/vod/tasks.py).
  * Dispatcharr rebuilds stream URLs as
    {account_server_url}/movie|series/{user}/{pass}/{stream_id}.{ext}
    and IGNORES `direct_source` for playback (apps/vod/models.py). So the stream
    endpoints below 302-redirect to the real provider URL; Dispatcharr's VOD
    proxy follows redirects (allow_redirects=True) and streams from the provider
    directly — our app is never in the byte path.
  * Empty strings (not null) are used for custom_sid/direct_source to avoid
    known client crashes on null values.
"""
from __future__ import annotations

import re
import time
import zlib
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from .db import DB

# Leading provider/language tag, e.g. "EN - ", "NF -  ", "D+ - ", "QFR - ".
# Requires whitespace after the separator so real titles like "X-Men" / "9-1-1"
# (no surrounding spaces) are left intact.
_PREFIX_RE = re.compile(r"^\s*[A-Za-z0-9+]{1,4}\s*[-|]\s+")
_YEAR_RE = re.compile(r"\((?:19|20)\d{2}\)")


def _now_ts() -> str:
    return str(int(time.time()))


def strip_prefix(name: str) -> str:
    return _PREFIX_RE.sub("", (name or "").strip(), count=1).strip()


def clean_title(name: str) -> str:
    """Turn a messy provider title into something matchable:
    'EN - Inception (2010) TOM HARDY, DICAPRIO' -> 'Inception (2010)'."""
    n = strip_prefix(name)
    m = _YEAR_RE.search(n)
    if m:
        n = n[: m.end()]
    n = re.sub(r"\s{2,}", " ", n).strip()
    return n or (name or "")


def cat_id(kind: str, group: str) -> str:
    return str(zlib.crc32(f"{kind}|{group}".encode("utf-8")) or 1)


def series_id_of(series_key: str) -> int:
    return zlib.crc32(series_key.encode("utf-8")) or 1


def _category_name(group: str) -> str:
    return group if group else "Uncategorized"


# --- auth / server info ---------------------------------------------------
def auth_response(username: str, password: str, base_url: str) -> dict:
    parts = urlsplit(base_url)
    scheme = parts.scheme or "http"
    host = parts.hostname or "localhost"
    port = parts.port or (443 if scheme == "https" else 80)
    now = int(time.time())
    return {
        "user_info": {
            "username": username,
            "password": password,
            "message": "",
            "auth": 1,
            "status": "Active",
            "exp_date": str(now + 10 * 365 * 24 * 3600),
            "is_trial": "0",
            "active_cons": "0",
            "created_at": str(now),
            "max_connections": "1",
            "allowed_output_formats": ["ts", "m3u8"],
        },
        "server_info": {
            "url": host,
            "port": str(port),
            "https_port": str(port),
            "server_protocol": scheme,
            "rtmp_port": "0",
            "timezone": "UTC",
            "timestamp_now": now,
            "time_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


# --- categories -----------------------------------------------------------
def _categories(db: DB, kind: str) -> list[dict]:
    out = []
    for c in db.led_categories(kind):
        out.append({
            "category_id": cat_id(kind, c["name"]),
            "category_name": _category_name(c["name"]),
            "parent_id": 0,
        })
    return out


def live_categories(db: DB) -> list[dict]:
    return _categories(db, "live")


def vod_categories(db: DB) -> list[dict]:
    return _categories(db, "movie")


def series_categories(db: DB) -> list[dict]:
    return _categories(db, "series")


def _resolve_group(db: DB, kind: str, category_id: Optional[str]) -> Optional[str]:
    """Map an Xtream category_id back to the real group name (None = all)."""
    if not category_id:
        return None
    for c in db.led_categories(kind):
        if cat_id(kind, c["name"]) == str(category_id):
            return c["name"]
    return None  # unknown id -> behave as 'all' rather than 404


# --- live / vod streams ---------------------------------------------------
def live_streams(db: DB, category_id: Optional[str] = None) -> list[dict]:
    group = _resolve_group(db, "live", category_id)
    rows = db.led_streams("live", group)
    ts = _now_ts()
    return [{
        "num": i + 1,
        "name": r["name"],
        "stream_type": "live",
        "stream_id": r["id"],
        "stream_icon": "",
        "epg_channel_id": "",
        "added": ts,
        "category_id": cat_id("live", r["group_title"]),
        "custom_sid": "",
        "tv_archive": 0,
        "direct_source": "",
        "tv_archive_duration": 0,
    } for i, r in enumerate(rows)]


def vod_streams(db: DB, category_id: Optional[str] = None) -> list[dict]:
    group = _resolve_group(db, "movie", category_id)
    rows = db.led_streams("movie", group)
    ts = _now_ts()
    return [{
        "num": i + 1,
        "name": clean_title(r["name"]),
        "stream_type": "movie",
        "stream_id": r["id"],
        "stream_icon": "",
        "rating": 0,
        "rating_5based": 0,
        "added": ts,
        "category_id": cat_id("movie", r["group_title"]),
        "container_extension": r["container_extension"],
        "custom_sid": "",
        "direct_source": "",
        "tmdb": r["tmdb"],
        "tmdb_id": r["tmdb"],
    } for i, r in enumerate(rows)]


def vod_info(db: DB, vod_id: str) -> dict:
    url = db.led_url(int(vod_id)) if str(vod_id).isdigit() else None
    rows = db.led_streams("movie", None)
    match = next((r for r in rows if str(r["id"]) == str(vod_id)), None)
    if not match:
        return {"info": {}, "movie_data": {}}
    return {
        "info": {
            "movie_image": "", "plot": "", "genre": "", "cast": "",
            "director": "", "rating": 0, "releasedate": "", "duration": "",
            "tmdb": match["tmdb"], "tmdb_id": match["tmdb"],
        },
        "movie_data": {
            "stream_id": match["id"],
            "name": clean_title(match["name"]),
            "added": _now_ts(),
            "category_id": cat_id("movie", match["group_title"]),
            "container_extension": match["container_extension"],
            "custom_sid": "",
            "direct_source": "",
        },
    }


# --- series ---------------------------------------------------------------
def series(db: DB, category_id: Optional[str] = None) -> list[dict]:
    group = _resolve_group(db, "series", category_id)
    rows = db.led_series(group)
    ts = _now_ts()
    return [{
        "num": i + 1,
        "series_id": series_id_of(r["series_key"]),
        "name": clean_title(r["name"]),
        "cover": "",
        "plot": "",
        "cast": "",
        "director": "",
        "genre": "",
        "releaseDate": "",
        "release_date": "",
        "last_modified": ts,
        "rating": 0,
        "rating_5based": 0,
        "category_id": cat_id("series", r["grp"]),
        "backdrop_path": [],
        "youtube_trailer": "",
        "episode_run_time": "",
        "tmdb": r["tmdb"],
        "tmdb_id": r["tmdb"],
    } for i, r in enumerate(rows)]


def series_info(db: DB, series_id: str) -> dict:
    # Resolve the numeric series_id back to a series_key.
    target = str(series_id)
    series_key = next(
        (k for k in db.led_series_keys() if str(series_id_of(k)) == target), None
    )
    if series_key is None:
        return {"info": {}, "seasons": [], "episodes": {}}

    eps = db.led_series_episodes(series_key)
    name = clean_title((eps[0]["series_name"] or eps[0]["name"]) if eps else "")
    tmdb = (eps[0]["tmdb"] if eps else "") or ""
    ts = _now_ts()
    episodes: dict[str, list] = {}
    season_counter: dict[int, int] = {}
    for e in eps:
        season = e["season"] if e["season"] is not None else 0
        season_counter[season] = season_counter.get(season, 0) + 1
        ep_num = e["episode"] if e["episode"] is not None else season_counter[season]
        episodes.setdefault(str(season), []).append({
            "id": str(e["id"]),
            "episode_num": ep_num,
            "title": strip_prefix(e["name"]),
            "container_extension": e["container_extension"],
            "added": ts,
            "season": season,
            "custom_sid": "",
            "direct_source": "",
            "info": {
                "plot": "", "duration": "", "movie_image": "",
                "rating": 0, "season": season, "tmdb_id": "",
            },
        })
    seasons = [{"season_number": s, "name": f"Season {s}"} for s in sorted(season_counter)]
    return {
        "info": {
            "name": name, "cover": "", "plot": "", "cast": "", "director": "",
            "genre": "", "releaseDate": "", "release_date": "", "rating": 0,
            "tmdb": tmdb, "tmdb_id": tmdb,
        },
        "seasons": seasons,
        "episodes": episodes,
    }


# --- action dispatch ------------------------------------------------------
def dispatch(db: DB, action: Optional[str], params: dict, base_url: str):
    """Return the JSON body for a player_api.php request."""
    username = params.get("username", "")
    password = params.get("password", "")
    if not action:
        return auth_response(username, password, base_url)

    category_id = params.get("category_id")
    if action == "get_live_categories":
        return live_categories(db)
    if action == "get_live_streams":
        return live_streams(db, category_id)
    if action == "get_vod_categories":
        return vod_categories(db)
    if action == "get_vod_streams":
        return vod_streams(db, category_id)
    if action in ("get_vod_info",):
        return vod_info(db, params.get("vod_id", ""))
    if action == "get_series_categories":
        return series_categories(db)
    if action == "get_series":
        return series(db, category_id)
    if action == "get_series_info":
        return series_info(db, params.get("series_id", ""))
    # Unknown/unsupported actions (EPG etc.) -> empty list is the safe default.
    return []


# --- stream redirect resolution ------------------------------------------
def resolve_redirect(db: DB, stream_id: str) -> Optional[str]:
    """The real provider URL for a stream id (movie/episode/live), for 302."""
    if not str(stream_id).isdigit():
        return None
    return db.led_url(int(stream_id))
