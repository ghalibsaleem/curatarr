"""Record curated picks into the ledger — the single source of truth for the
Xtream wrapper that Dispatcharr consumes.

Picks (live, movies, series episodes) are stored in the ledger only; the Xtream
wrapper serves all of them. Imports are idempotent by content hash.
"""
from __future__ import annotations

from .db import DB


def import_rows(db: DB, rows: list) -> dict:
    """rows: sqlite3.Row carrying the ledger pick columns. Records new picks in
    the ledger (idempotent). Returns a per-kind summary."""
    already = db.is_imported(r["hash"] for r in rows)
    new_rows = [r for r in rows if r["hash"] not in already]
    if new_rows:
        db.mark_imported(new_rows)

    by_kind: dict[str, int] = {}
    for r in new_rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    return {
        "requested": len(rows),
        "imported": len(new_rows),
        "skipped_existing": len(rows) - len(new_rows),
        "by_kind": by_kind,
    }
