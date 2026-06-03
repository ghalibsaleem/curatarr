"""Key/value app settings persisted in the `meta` table."""
from __future__ import annotations

from typing import Optional

from ..db import Database


class MetaRepo:
    def __init__(self, db: Database):
        self._conn = db.conn

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()
