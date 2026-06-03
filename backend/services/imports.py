"""Import / un-import curated picks into the ledger (idempotent by hash)."""
from __future__ import annotations

from typing import Optional

from ..errors import ConfigError, NotFoundError
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

    def unimport(self, ids: list[int]) -> int:
        return self.ledger.remove(ids)

    def imported_list(self) -> list[dict]:
        return self.ledger.list_all()
