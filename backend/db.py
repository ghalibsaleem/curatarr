"""Database infrastructure: the SQLite connection, schema, and migrations.

Two tables:
  * items    — the scan cache, rebuilt on every sync (one row per live/movie/series).
  * imported — the curated ledger (the source of truth for what we expose to
               Dispatcharr), keyed by a content hash so it survives re-scans.

Query logic lives in the repository layer (backend/repositories), not here.
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    group_title TEXT NOT NULL DEFAULT '',
    tvg_id      TEXT NOT NULL DEFAULT '',
    tvg_logo    TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL,
    extinf      TEXT NOT NULL,
    series_key  TEXT NOT NULL DEFAULT '',
    series_name TEXT NOT NULL DEFAULT '',
    season      INTEGER,
    episode     INTEGER,
    tmdb        TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_kind   ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_group  ON items(kind, group_title);
CREATE INDEX IF NOT EXISTS idx_items_series ON items(series_key);
CREATE INDEX IF NOT EXISTS idx_items_name   ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_hash   ON items(hash);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# The imported ledger. `id` is stable (AUTOINCREMENT) and used as the Xtream
# stream_id for movies and episodes. Migrated independently of the scan cache.
IMPORTED_DDL = """
CREATE TABLE imported (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hash         TEXT UNIQUE NOT NULL,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    group_title  TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    extinf       TEXT NOT NULL DEFAULT '',
    series_key   TEXT NOT NULL DEFAULT '',
    series_name  TEXT NOT NULL DEFAULT '',
    season       INTEGER,
    episode      INTEGER,
    container_extension TEXT NOT NULL DEFAULT 'mp4',
    tmdb         TEXT NOT NULL DEFAULT '',
    provider_id  TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'app',
    imported_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_imp_kind   ON imported(kind);
CREATE INDEX IF NOT EXISTS idx_imp_series ON imported(series_key);
"""


class Database:
    """Owns the SQLite connection. Repositories read `db.conn`."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Bring an existing DB up to the current schema. Old builds had a
        minimal imported(hash,kind,name) table with no URL/season data — those
        rows can't be served via Xtream, so recreate it. Newer columns are added
        in place to preserve existing picks."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(imported)")}
        if not cols:
            self.conn.executescript(IMPORTED_DDL)
        elif "url" not in cols:
            self.conn.execute("DROP TABLE imported")
            self.conn.executescript(IMPORTED_DDL)
        self._ensure_column("items", "tmdb", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("imported", "tmdb", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("items", "provider_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("imported", "provider_id", "TEXT NOT NULL DEFAULT ''")
        # Lazily-cached total season/episode counts per series (NULL = unknown).
        self._ensure_column("items", "total_seasons", "INTEGER")
        self._ensure_column("items", "total_episodes", "INTEGER")

    def _ensure_column(self, table: str, col: str, decl: str) -> None:
        cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
