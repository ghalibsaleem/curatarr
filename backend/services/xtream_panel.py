"""Build the curated Xtream Codes panel served to Dispatcharr from the ledger.

Design notes (verified against Dispatcharr v0.25.1 source):
  * get_series_info returns episodes as a SEASON-KEYED OBJECT ({"1":[...]}), not
    an array — Dispatcharr iterates episodes.items() and uses each episode's `id`
    as the stream id.
  * Dispatcharr rebuilds stream URLs as {server}/movie|series/{u}/{p}/{id}.{ext}
    and ignores direct_source; its VOD proxy follows redirects. So our stream
    endpoints 302 to the real provider URL and we stay out of the byte path.
  * Empty strings (not null) for custom_sid/direct_source avoid client crashes.
"""
from __future__ import annotations

import re
import time
import zlib
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from ..providers.xtream_client import stream_url
from ..repositories.ledger import LedgerRepo

# Leading provider/language tag, e.g. "EN - ", "NF -  ", "D+ - ". Requires
# whitespace after the separator so "X-Men" / "9-1-1" are left intact.
_PREFIX_RE = re.compile(r"^\s*[A-Za-z0-9+]{1,4}\s*[-|]\s+")
_YEAR_RE = re.compile(r"\((?:19|20)\d{2}\)")


def _now_ts() -> str:
    return str(int(time.time()))


def strip_prefix(name: str) -> str:
    return _PREFIX_RE.sub("", (name or "").strip(), count=1).strip()


def clean_title(name: str) -> str:
    """'EN - Inception (2010) TOM HARDY, DICAPRIO' -> 'Inception (2010)'."""
    n = strip_prefix(name)
    m = _YEAR_RE.search(n)
    if m:
        n = n[: m.end()]
    return re.sub(r"\s{2,}", " ", n).strip() or (name or "")


def cat_id(kind: str, group: str) -> str:
    return str(zlib.crc32(f"{kind}|{group}".encode("utf-8")) or 1)


def series_id_of(series_key: str) -> int:
    return zlib.crc32(series_key.encode("utf-8")) or 1


def _category_name(group: str) -> str:
    return group if group else "Uncategorized"


def auth_response(username: str, password: str, base_url: str) -> dict:
    parts = urlsplit(base_url)
    scheme = parts.scheme or "http"
    host = parts.hostname or "localhost"
    port = parts.port or (443 if scheme == "https" else 80)
    now = int(time.time())
    return {
        "user_info": {
            "username": username, "password": password, "message": "",
            "auth": 1, "status": "Active",
            "exp_date": str(now + 10 * 365 * 24 * 3600), "is_trial": "0",
            "active_cons": "0", "created_at": str(now), "max_connections": "1",
            "allowed_output_formats": ["ts", "m3u8"],
        },
        "server_info": {
            "url": host, "port": str(port), "https_port": str(port),
            "server_protocol": scheme, "rtmp_port": "0", "timezone": "UTC",
            "timestamp_now": now,
            "time_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


class XtreamPanelService:
    def __init__(self, ledger: LedgerRepo):
        self.ledger = ledger

    # --- categories -------------------------------------------------------
    def _categories(self, kind: str) -> list[dict]:
        return [{
            "category_id": cat_id(kind, c["name"]),
            "category_name": _category_name(c["name"]),
            "parent_id": 0,
        } for c in self.ledger.categories(kind)]

    def _resolve_group(self, kind: str, category_id: Optional[str]) -> Optional[str]:
        if not category_id:
            return None
        for c in self.ledger.categories(kind):
            if cat_id(kind, c["name"]) == str(category_id):
                return c["name"]
        return None  # unknown id -> behave as 'all'

    # --- streams ----------------------------------------------------------
    def live_streams(self, category_id=None) -> list[dict]:
        rows = self.ledger.streams("live", self._resolve_group("live", category_id))
        ts = _now_ts()
        return [{
            "num": i + 1, "name": r["name"], "stream_type": "live",
            "stream_id": r["id"], "stream_icon": "", "epg_channel_id": "",
            "added": ts, "category_id": cat_id("live", r["group_title"]),
            "custom_sid": "", "tv_archive": 0, "direct_source": "",
            "tv_archive_duration": 0,
        } for i, r in enumerate(rows)]

    def vod_streams(self, category_id=None) -> list[dict]:
        rows = self.ledger.streams("movie", self._resolve_group("movie", category_id))
        ts = _now_ts()
        return [{
            "num": i + 1, "name": clean_title(r["name"]), "stream_type": "movie",
            "stream_id": r["id"], "stream_icon": "", "rating": 0, "rating_5based": 0,
            "added": ts, "category_id": cat_id("movie", r["group_title"]),
            "container_extension": r["container_extension"], "custom_sid": "",
            "direct_source": "", "tmdb": r["tmdb"], "tmdb_id": r["tmdb"],
        } for i, r in enumerate(rows)]

    def vod_info(self, vod_id: str) -> dict:
        rows = self.ledger.streams("movie", None)
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
                "stream_id": match["id"], "name": clean_title(match["name"]),
                "added": _now_ts(), "category_id": cat_id("movie", match["group_title"]),
                "container_extension": match["container_extension"],
                "custom_sid": "", "direct_source": "",
            },
        }

    # --- series -----------------------------------------------------------
    def series(self, category_id=None) -> list[dict]:
        rows = self.ledger.series(self._resolve_group("series", category_id))
        ts = _now_ts()
        return [{
            "num": i + 1, "series_id": series_id_of(r["series_key"]),
            "name": clean_title(r["name"]), "cover": "", "plot": "", "cast": "",
            "director": "", "genre": "", "releaseDate": "", "release_date": "",
            "last_modified": ts, "rating": 0, "rating_5based": 0,
            "category_id": cat_id("series", r["grp"]), "backdrop_path": [],
            "youtube_trailer": "", "episode_run_time": "",
            "tmdb": r["tmdb"], "tmdb_id": r["tmdb"],
        } for i, r in enumerate(rows)]

    def series_info(self, series_id: str) -> dict:
        target = str(series_id)
        series_key = next(
            (k for k in self.ledger.series_keys() if str(series_id_of(k)) == target), None
        )
        if series_key is None:
            return {"info": {}, "seasons": [], "episodes": {}}
        eps = self.ledger.series_episodes(series_key)
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
                "id": str(e["id"]), "episode_num": ep_num,
                "title": strip_prefix(e["name"]),
                "container_extension": e["container_extension"], "added": ts,
                "season": season, "custom_sid": "", "direct_source": "",
                "info": {"plot": "", "duration": "", "movie_image": "",
                         "rating": 0, "season": season, "tmdb_id": ""},
            })
        seasons = [{"season_number": s, "name": f"Season {s}"} for s in sorted(season_counter)]
        return {
            "info": {
                "name": name, "cover": "", "plot": "", "cast": "", "director": "",
                "genre": "", "releaseDate": "", "release_date": "", "rating": 0,
                "tmdb": tmdb, "tmdb_id": tmdb,
            },
            "seasons": seasons, "episodes": episodes,
        }

    # --- dispatch + redirect ---------------------------------------------
    def dispatch(self, action: Optional[str], params: dict, base_url: str):
        if not action:
            return auth_response(params.get("username", ""), params.get("password", ""), base_url)
        category_id = params.get("category_id")
        handlers = {
            "get_live_categories": lambda: self._categories("live"),
            "get_live_streams": lambda: self.live_streams(category_id),
            "get_vod_categories": lambda: self._categories("movie"),
            "get_vod_streams": lambda: self.vod_streams(category_id),
            "get_vod_info": lambda: self.vod_info(params.get("vod_id", "")),
            "get_series_categories": lambda: self._categories("series"),
            "get_series": lambda: self.series(category_id),
            "get_series_info": lambda: self.series_info(params.get("series_id", "")),
        }
        handler = handlers.get(action)
        return handler() if handler else []  # unknown actions -> empty

    def redirect_url(self, sub: dict | None, stream_id: str) -> Optional[str]:
        """Provider URL for a stream id, rebuilt with the output account's sub
        credentials (so Dispatcharr can load-balance the pick across subs)."""
        ref = self.ledger.stream_ref(stream_id)
        if not ref:
            return None
        if sub and ref["provider_id"]:
            return stream_url(sub["base"], sub["user"], sub["pass"],
                              ref["kind"], ref["provider_id"], ref["container_extension"])
        # Fallback for picks imported before provider_id existed.
        return self.ledger.url(int(stream_id)) if str(stream_id).isdigit() else None
