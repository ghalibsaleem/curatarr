"""Browsing the catalogue: combines the scan cache (items) with the ledger for
imported flags, and lazily fetches per-series totals/episodes from the provider.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..errors import ProviderError
from ..providers.xtream_client import stream_hash
from ..repositories.items import ItemsRepo
from ..repositories.ledger import LedgerRepo
from .subscriptions import SubscriptionsService


class CatalogService:
    def __init__(self, subs: SubscriptionsService, items: ItemsRepo, ledger: LedgerRepo):
        self.subs = subs
        self.items = items
        self.ledger = ledger

    def counts(self) -> dict:
        out = self.items.kind_counts()
        out["imported"] = self.ledger.count()
        return out

    def groups(self, kind: str) -> list[dict]:
        return self.items.groups(kind)

    def list_items(self, kind, group, q, page, page_size) -> dict:
        offset = (max(page, 1) - 1) * page_size
        rows, total = self.items.page(kind, group, q, page_size, offset)
        imported = self.ledger.imported_hashes(r["hash"] for r in rows)
        out = [{
            "id": r["id"], "name": r["name"], "group": r["group_title"],
            "imported": r["hash"] in imported,
        } for r in rows]
        return {"items": out, "total": total, "page": page, "page_size": page_size}

    def list_series(self, group, q, page, page_size) -> dict:
        offset = (max(page, 1) - 1) * page_size
        rows, total = self.items.series_page(group, q, page_size, offset)
        counts = self.ledger.imported_series_counts([r["series_key"] for r in rows])
        out = [{
            "series_key": r["series_key"], "name": r["name"], "group": r["group_title"],
            "imported": r["series_key"] in counts,
            "imp_seasons": counts.get(r["series_key"], {}).get("seasons", 0),
            "imp_episodes": counts.get(r["series_key"], {}).get("episodes", 0),
            "total_seasons": r["total_seasons"], "total_episodes": r["total_episodes"],
        } for r in rows]
        return {"series": out, "total": total, "page": page, "page_size": page_size}

    def series_counts(self, keys: list[str]) -> dict:
        """Total season/episode counts; cached, uncached fetched concurrently."""
        keys = list(dict.fromkeys(keys))[:60]
        out = self.items.cached_series_totals(keys)
        missing = [k for k in keys if k not in out]
        if missing:
            xc = self.subs.primary_provider()

            def fetch(k):
                try:
                    eps = (xc.series_info(k).get("episodes") or {})
                    return k, len(eps), sum(len(v) for v in eps.values())
                except Exception:
                    return k, None, None

            with ThreadPoolExecutor(max_workers=6) as ex:
                for k, seasons, episodes in ex.map(fetch, missing):
                    if seasons is not None:
                        self.items.set_series_total(k, seasons, episodes)
                        out[k] = {"seasons": seasons, "episodes": episodes}
        return out

    def episode_rows(self, series_key: str) -> list[dict]:
        """Fetch a series' episodes from the provider (lazy) as ledger-shaped
        dicts. Shared by series detail (browse) and import."""
        xc = self.subs.primary_provider()
        meta = self.items.series_row(series_key)
        name = meta["name"] if meta else ""
        group = meta["group_title"] if meta else ""
        tmdb = meta["tmdb"] if meta else ""
        try:
            info = xc.series_info(series_key)
        except Exception as e:
            raise ProviderError(f"Xtream series fetch failed: {e}")
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

    def series_detail(self, series_key: str) -> dict:
        rows = self.episode_rows(series_key)
        imported = self.ledger.imported_hashes(r["hash"] for r in rows)
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
