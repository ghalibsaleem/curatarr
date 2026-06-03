"""Append curated picks to the destination M3U, byte-faithfully and idempotently.

An "import" copies the source entry's #EXTINF line and URL verbatim into the
destination file. Already-imported entries (tracked by content hash) are skipped,
so re-importing is a no-op and the destination never gets duplicates.
"""
from __future__ import annotations

import os

from .db import DB


def _ensure_header(dest_path: str) -> None:
    if not os.path.exists(dest_path) or os.path.getsize(dest_path) == 0:
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
        with open(dest_path, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")


def import_rows(db: DB, dest_path: str, rows: list) -> dict:
    """rows: sqlite3.Row with id,kind,name,extinf,url,hash.

    Returns a summary dict. Only rows not already in the imported ledger are
    written to disk and marked.
    """
    already = db.is_imported(r["hash"] for r in rows)
    new_rows = [r for r in rows if r["hash"] not in already]

    if new_rows:
        _ensure_header(dest_path)
        with open(dest_path, "a", encoding="utf-8") as fh:
            for r in new_rows:
                fh.write(r["extinf"].rstrip("\n") + "\n")
                fh.write(r["url"].rstrip("\n") + "\n")
        db.mark_imported([(r["hash"], r["kind"], r["name"]) for r in new_rows])

    return {
        "requested": len(rows),
        "imported": len(new_rows),
        "skipped_existing": len(rows) - len(new_rows),
        "dest": dest_path,
    }
