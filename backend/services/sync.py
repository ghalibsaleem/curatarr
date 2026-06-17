"""Pull the provider's Xtream catalogue into the scan cache.

Movies are fetched per category (the provider returns nothing for
get_vod_streams without a category_id). Series contribute one row each; their
episodes are fetched lazily on open/import.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from ..errors import ProviderError
from ..providers.xtream_client import stream_hash
from ..repositories.items import ItemsRepo
from ..repositories.ledger import LedgerRepo
from ..repositories.meta import MetaRepo
from .subscriptions import SubscriptionsService


def _meta(fields: dict) -> str:
    """JSON-encode forwarded metadata, dropping empty values to stay compact.
    The panel defaults anything absent, so empties don't need storing."""
    kept = {k: v for k, v in fields.items() if v not in ("", 0, [], None, "0")}
    return json.dumps(kept, separators=(",", ":")) if kept else ""


def _live_meta(s: dict) -> str:
    return _meta({
        "stream_icon": s.get("stream_icon"),
        "epg_channel_id": s.get("epg_channel_id"),
        "tv_archive": s.get("tv_archive"),
        "tv_archive_duration": s.get("tv_archive_duration"),
        "added": s.get("added"),
    })


def _movie_meta(s: dict) -> str:
    return _meta({
        "stream_icon": s.get("stream_icon"),
        "rating": s.get("rating"),
        "rating_5based": s.get("rating_5based"),
        "added": s.get("added"),
    })


def _series_meta(s: dict) -> str:
    return _meta({
        "cover": s.get("cover"),
        "plot": s.get("plot"),
        "cast": s.get("cast"),
        "director": s.get("director"),
        "genre": s.get("genre"),
        "rating": s.get("rating"),
        "rating_5based": s.get("rating_5based"),
        "releaseDate": s.get("releaseDate") or s.get("release_date"),
        "backdrop_path": s.get("backdrop_path"),
        "youtube_trailer": s.get("youtube_trailer"),
        "episode_run_time": s.get("episode_run_time"),
        "last_modified": s.get("last_modified"),
    })


class SyncService:
    def __init__(self, subs: SubscriptionsService, items: ItemsRepo,
                 ledger: LedgerRepo, meta: MetaRepo):
        self.subs = subs
        self.items = items
        self.ledger = ledger
        self.meta = meta
        # Serialize syncs so a scheduled run and a manual "Sync now" can't both
        # rebuild the scan cache at once.
        self._lock = threading.Lock()

    def run(self) -> int:
        """Rebuild the scan cache from the provider. Returns parsed item count."""
        with self._lock:
            return self._run()

    def _run(self) -> int:
        xc = self.subs.primary_provider()
        try:
            xc.authenticate()
        except Exception as e:
            raise ProviderError(f"Xtream auth failed: {e}")

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
                    "hash": stream_hash(url), "metadata": _live_meta(s),
                })
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
                        "hash": stream_hash(url), "metadata": _movie_meta(s),
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
                    "hash": f"series:{sid}", "metadata": _series_meta(s),
                })
        except Exception as e:
            raise ProviderError(f"Xtream fetch failed: {e}")

        count = self.items.replace_all(rows)
        self.ledger.backfill_from_items()  # existing picks gain tmdb + provider_id
        self.ledger.backfill_metadata_from_items()  # …and forwarded metadata
        self.meta.set("last_sync", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return count
