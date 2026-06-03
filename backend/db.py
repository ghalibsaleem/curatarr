"""SQLite storage: the scan cache (items) + the imported ledger.

`items` is rebuilt on every scan. `imported` is the curated selection and the
single source of truth for what we expose to Dispatcharr (via the Xtream wrapper)
and write to the curated M3U. It stores each pick's full data (URL, EXTINF,
season/episode, container extension) keyed by a content hash, so it survives
re-scans and can be served/rebuilt without the source being present.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional
from urllib.parse import urlsplit

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
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_kind        ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_group       ON items(kind, group_title);
CREATE INDEX IF NOT EXISTS idx_items_series      ON items(series_key);
CREATE INDEX IF NOT EXISTS idx_items_name        ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_hash        ON items(hash);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# The imported ledger. `id` is stable (AUTOINCREMENT) and used as the Xtream
# stream_id for movies and episodes. Kept separate so the schema can be migrated
# independently of the scan cache.
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
    source       TEXT NOT NULL DEFAULT 'app',
    imported_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_imp_kind   ON imported(kind);
CREATE INDEX IF NOT EXISTS idx_imp_series ON imported(series_key);
"""


def container_ext(url: str, kind: str) -> str:
    """Best-effort container extension for Xtream. Live has none -> 'ts'."""
    if kind == "live":
        return "ts"
    last = urlsplit(url).path.rsplit("/", 1)[-1]
    if "." in last:
        ext = last.rsplit(".", 1)[-1].lower()
        if 1 <= len(ext) <= 5 and ext.isalnum():
            return ext
    return "mp4"


class DB:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate_imported()
        self._conn.commit()

    def _migrate_imported(self) -> None:
        """Create (or upgrade) the imported ledger to the current schema. Old
        builds had a minimal (hash,kind,name) table with no URL/season data —
        those rows can't be served via Xtream, so we recreate the table.
        Newer columns (tmdb) are added in place to preserve existing picks."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(imported)")}
        if not cols:
            self._conn.executescript(IMPORTED_DDL)
        elif "url" not in cols:
            self._conn.execute("DROP TABLE imported")
            self._conn.executescript(IMPORTED_DDL)
        self._ensure_column("items", "tmdb", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("imported", "tmdb", "TEXT NOT NULL DEFAULT ''")

    def _ensure_column(self, table: str, col: str, decl: str) -> None:
        cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # --- scan -------------------------------------------------------------
    @staticmethod
    def _insert_batch(cur, batch):
        cur.executemany(
            """INSERT INTO items
               (kind,name,group_title,tvg_id,tvg_logo,url,extinf,
                series_key,series_name,season,episode,tmdb,hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )

    def replace_items_rows(self, rows: Iterable[dict]) -> int:
        """Rebuild the scan cache from Xtream-derived dict rows. Each dict has
        kind/name/group_title/tvg_logo/url/series_key/series_name/season/episode/hash.
        For series there is one row per series (episodes are fetched lazily)."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM items")
        count = 0
        batch: list[tuple] = []
        for r in rows:
            batch.append((
                r["kind"], r["name"], r.get("group_title", ""), "",
                r.get("tvg_logo", ""), r.get("url", ""), "",
                r.get("series_key", ""), r.get("series_name", ""),
                r.get("season"), r.get("episode"), r.get("tmdb", ""), r["hash"],
            ))
            if len(batch) >= 5000:
                self._insert_batch(cur, batch)
                count += len(batch)
                batch.clear()
        if batch:
            self._insert_batch(cur, batch)
            count += len(batch)
        self._conn.commit()
        self.set_meta("item_count", str(count))
        return count

    # --- meta -------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # --- browse (scan cache) ---------------------------------------------
    def counts(self) -> dict:
        rows = self._conn.execute(
            "SELECT kind, COUNT(*) n FROM items GROUP BY kind"
        ).fetchall()
        out = {"live": 0, "movie": 0, "series": 0}
        for r in rows:
            out[r["kind"]] = r["n"]
        out["imported"] = self._conn.execute(
            "SELECT COUNT(*) n FROM imported"
        ).fetchone()["n"]
        return out

    def groups(self, kind: str) -> list[dict]:
        if kind == "series":
            rows = self._conn.execute(
                "SELECT group_title AS name, COUNT(DISTINCT series_key) AS n "
                "FROM items WHERE kind='series' GROUP BY group_title "
                "ORDER BY group_title",
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT group_title AS name, COUNT(*) AS n FROM items "
                "WHERE kind=? GROUP BY group_title ORDER BY group_title",
                (kind,),
            ).fetchall()
        return [{"name": r["name"], "count": r["n"]} for r in rows]

    def items(self, kind: str, group: Optional[str], q: Optional[str],
              limit: int, offset: int) -> tuple[list[dict], int]:
        where = ["kind=?"]
        args: list = [kind]
        if group:
            where.append("group_title=?")
            args.append(group)
        if q:
            where.append("name LIKE ?")
            args.append(f"%{q}%")
        clause = " AND ".join(where)
        total = self._conn.execute(
            f"SELECT COUNT(*) n FROM items WHERE {clause}", args
        ).fetchone()["n"]
        rows = self._conn.execute(
            f"""SELECT id,name,group_title,url,hash FROM items
                WHERE {clause} ORDER BY name LIMIT ? OFFSET ?""",
            (*args, limit, offset),
        ).fetchall()
        imported = self._imported_set([r["hash"] for r in rows])
        out = [{
            "id": r["id"], "name": r["name"], "group": r["group_title"],
            "imported": r["hash"] in imported,
        } for r in rows]
        return out, total

    def series_list(self, group: Optional[str], q: Optional[str],
                    limit: int, offset: int) -> tuple[list[dict], int]:
        # One row per series in the scan cache (episodes are fetched lazily).
        where = ["kind='series'"]
        args: list = []
        if group:
            where.append("group_title=?")
            args.append(group)
        if q:
            where.append("name LIKE ?")
            args.append(f"%{q}%")
        clause = " AND ".join(where)
        total = self._conn.execute(
            f"SELECT COUNT(*) n FROM items WHERE {clause}", args
        ).fetchone()["n"]
        rows = self._conn.execute(
            f"""SELECT series_key, name, group_title FROM items WHERE {clause}
                ORDER BY name LIMIT ? OFFSET ?""",
            (*args, limit, offset),
        ).fetchall()
        imported = self._imported_series_keys([r["series_key"] for r in rows])
        out = [{
            "series_key": r["series_key"], "name": r["name"],
            "group": r["group_title"], "imported": r["series_key"] in imported,
        } for r in rows]
        return out, total

    def _imported_series_keys(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        marks = ",".join("?" * len(keys))
        rows = self._conn.execute(
            f"SELECT DISTINCT series_key FROM imported "
            f"WHERE kind='series' AND series_key IN ({marks})", keys
        ).fetchall()
        return {r["series_key"] for r in rows}

    def series_row(self, series_key: str) -> Optional[sqlite3.Row]:
        """The scan-cache row for a series (name/group/tmdb), used to label
        episodes fetched lazily from the provider."""
        return self._conn.execute(
            "SELECT name, group_title, tmdb FROM items "
            "WHERE kind='series' AND series_key=? LIMIT 1", (series_key,)
        ).fetchone()

    _PICK_COLS = ("id,kind,name,group_title,url,extinf,series_key,series_name,"
                  "season,episode,tmdb,hash")

    def rows_for_ids(self, ids: list[int]) -> list[sqlite3.Row]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self._conn.execute(
            f"SELECT {self._PICK_COLS} FROM items WHERE id IN ({marks})", ids,
        ).fetchall()

    # --- imported ledger (curated selection) -----------------------------
    def _imported_set(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        marks = ",".join("?" * len(hashes))
        rows = self._conn.execute(
            f"SELECT hash FROM imported WHERE hash IN ({marks})", hashes
        ).fetchall()
        return {r["hash"] for r in rows}

    def is_imported(self, hashes: Iterable[str]) -> set[str]:
        return self._imported_set(list(hashes))

    def mark_imported(self, rows, source: str = "app") -> int:
        """Insert full pick records into the ledger (idempotent by hash).
        `rows` are sqlite3.Row/dicts carrying the _PICK_COLS fields."""
        recs = [(
            r["hash"], r["kind"], r["name"], r["group_title"], r["url"],
            r["extinf"], r["series_key"], r["series_name"],
            r["season"], r["episode"], container_ext(r["url"], r["kind"]),
            r["tmdb"], source,
        ) for r in rows]
        cur = self._conn.cursor()
        before = cur.execute("SELECT COUNT(*) n FROM imported").fetchone()["n"]
        cur.executemany(
            """INSERT OR IGNORE INTO imported
               (hash,kind,name,group_title,url,extinf,series_key,series_name,
                season,episode,container_extension,tmdb,source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            recs,
        )
        self._conn.commit()
        after = cur.execute("SELECT COUNT(*) n FROM imported").fetchone()["n"]
        return after - before

    def remove_imported(self, ids: list[int]) -> int:
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = self._conn.cursor()
        cur.execute(f"DELETE FROM imported WHERE id IN ({marks})", ids)
        self._conn.commit()
        return cur.rowcount

    def imported_list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id,kind,name,group_title,season,episode,imported_at "
            "FROM imported ORDER BY imported_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Xtream wrapper queries (served from the ledger) -----------------
    def led_categories(self, kind: str) -> list[dict]:
        """Distinct groups in the ledger for a kind, with item/series counts."""
        if kind == "series":
            rows = self._conn.execute(
                "SELECT group_title AS name, COUNT(DISTINCT series_key) AS n "
                "FROM imported WHERE kind='series' GROUP BY group_title "
                "ORDER BY group_title"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT group_title AS name, COUNT(*) AS n FROM imported "
                "WHERE kind=? GROUP BY group_title ORDER BY group_title", (kind,)
            ).fetchall()
        return [{"name": r["name"], "count": r["n"]} for r in rows]

    def led_streams(self, kind: str, group: Optional[str]) -> list[sqlite3.Row]:
        """Flat ledger rows for live or movie."""
        sql = ("SELECT id,name,group_title,url,container_extension,tmdb "
               "FROM imported WHERE kind=?")
        args: list = [kind]
        if group is not None:
            sql += " AND group_title=?"
            args.append(group)
        sql += " ORDER BY name"
        return self._conn.execute(sql, args).fetchall()

    def led_series(self, group: Optional[str]) -> list[sqlite3.Row]:
        """Distinct series in the ledger."""
        sql = ("SELECT series_key, MIN(series_name) AS name, MIN(group_title) AS grp, "
               "MAX(tmdb) AS tmdb, COUNT(*) AS episodes FROM imported WHERE kind='series'")
        args: list = []
        if group is not None:
            sql += " AND group_title=?"
            args.append(group)
        sql += " GROUP BY series_key ORDER BY name"
        return self._conn.execute(sql, args).fetchall()

    def led_series_keys(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT series_key FROM imported WHERE kind='series'"
        ).fetchall()
        return [r["series_key"] for r in rows]

    def led_series_episodes(self, series_key: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT id,name,series_name,season,episode,container_extension,url,tmdb "
            "FROM imported WHERE kind='series' AND series_key=? "
            "ORDER BY season,episode", (series_key,)
        ).fetchall()

    def led_url(self, stream_id: int) -> Optional[str]:
        row = self._conn.execute(
            "SELECT url FROM imported WHERE id=?", (stream_id,)
        ).fetchone()
        return row["url"] if row else None

    def backfill_ledger_tmdb(self) -> int:
        """After a sync, populate tmdb on existing ledger picks from the freshly
        rebuilt scan cache (matched by hash), so already-imported items gain
        TMDB ids without needing re-import."""
        cur = self._conn.cursor()
        cur.execute(
            "UPDATE imported SET tmdb = COALESCE("
            "  (SELECT i.tmdb FROM items i WHERE i.hash = imported.hash), tmdb) "
            "WHERE (tmdb IS NULL OR tmdb = '')"
        )
        self._conn.commit()
        return cur.rowcount
