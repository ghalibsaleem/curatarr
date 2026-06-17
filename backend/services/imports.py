"""Import / un-import curated picks into the ledger (idempotent by hash)."""
from __future__ import annotations

import json
from typing import Optional

from .. import m3u
from ..errors import ConfigError, NotFoundError
from ..providers.xtream_client import stream_hash, stream_url
from ..repositories.items import ItemsRepo
from ..repositories.ledger import LedgerRepo
from .catalog import CatalogService


class ImportService:
    def __init__(self, items: ItemsRepo, ledger: LedgerRepo, catalog: CatalogService):
        self.items = items
        self.ledger = ledger
        self.catalog = catalog

    def do_import(self, ids: Optional[list[int]], series_key: Optional[str],
                  season: Optional[int], episode_ids: Optional[list[str]]) -> dict:
        if series_key:
            rows = self.catalog.episode_rows(series_key)
            if season is not None:
                rows = [r for r in rows if r["season"] == season]
            if episode_ids:
                wanted = set(map(str, episode_ids))
                rows = [r for r in rows if r["ep_id"] in wanted]
        elif ids:
            rows = self.items.rows_for_ids(ids)
        else:
            raise ConfigError("Provide ids or series_key")
        if not rows:
            raise NotFoundError("Nothing matched")
        return self._record(rows)

    def _record(self, rows) -> dict:
        already = self.ledger.imported_hashes(r["hash"] for r in rows)
        new_rows = [r for r in rows if r["hash"] not in already]
        if new_rows:
            self.ledger.mark(new_rows)
        by_kind: dict[str, int] = {}
        for r in new_rows:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        return {
            "requested": len(rows),
            "imported": len(new_rows),
            "skipped_existing": len(rows) - len(new_rows),
            "by_kind": by_kind,
        }

    def import_m3u(self, text: str) -> dict:
        """Bulk-import an existing curated playlist by matching each entry's
        provider stream id (sub-independent) against the current catalogue."""
        subs = self.catalog.subs.get_subs()
        if not subs:
            raise ConfigError("Add a subscription and Sync before importing a playlist")
        primary = subs[0]

        movie_pids: dict[str, dict] = {}
        live_pids: dict[str, dict] = {}
        series_entries: list[dict] = []
        failed = 0
        total = 0
        for e in m3u.parse(text):
            total += 1
            kind, pid, ext = m3u.classify_url(e["url"])
            if not pid:
                failed += 1
                continue
            if kind == "movie":
                movie_pids.setdefault(pid, e)
            elif kind == "live":
                live_pids.setdefault(pid, e)
            else:
                season, episode, sname = m3u.split_episode(e["name"])
                series_entries.append({
                    "norm": m3u.norm(sname), "season": season, "episode": episode,
                    "pid": pid, "ext": ext or "mp4", "name": e["name"], "group": e["group"],
                })

        rows: list = []
        not_found: list[str] = []

        for kind, bucket in (("movie", movie_pids), ("live", live_pids)):
            matched = {r["provider_id"]: r for r in self.items.rows_by_provider_ids(kind, bucket.keys())}
            for pid, e in bucket.items():
                row = matched.get(pid)
                (rows.append(row) if row else not_found.append(e["name"]))

        if series_entries:
            sidx = {m3u.norm(r["name"]): r for r in self.items.all_series()}
            for se in series_entries:
                srow = sidx.get(se["norm"])
                if not srow:
                    not_found.append(se["name"])
                    continue
                url = stream_url(primary["base"], primary["user"], primary["pass"],
                                 "series", se["pid"], se["ext"])
                smeta = srow["metadata"] if "metadata" in srow.keys() else ""
                rows.append({
                    "kind": "series", "name": se["name"],
                    "group_title": srow["group_title"] or se["group"], "url": url,
                    "extinf": "", "series_key": srow["series_key"],
                    "series_name": srow["name"],
                    "season": se["season"] if se["season"] is not None else 0,
                    "episode": se["episode"], "tmdb": srow["tmdb"],
                    "provider_id": se["pid"], "hash": stream_hash(url),
                    # M3U import has no episode detail; carry series-level only.
                    "metadata": json.dumps({"series": json.loads(smeta)},
                                           separators=(",", ":")) if smeta else "",
                })

        summary = self._record(rows)
        return {
            "total": total,
            "imported": summary["imported"],
            "already": summary["skipped_existing"],
            "not_found": len(not_found),
            "failed": failed,
            "by_kind": summary["by_kind"],
            "not_found_samples": not_found[:25],
        }

    def unimport(self, ids: list[int]) -> int:
        return self.ledger.remove(ids)

    def imported_list(self) -> list[dict]:
        return self.ledger.list_all()
