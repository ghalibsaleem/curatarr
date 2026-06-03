"""SQLite storage: the scan cache (items) + the imported ledger.

`items` is rebuilt on every scan. `imported` survives rescans (keyed by the
content hash of #EXTINF+URL) so already-curated picks stay marked even when the
source is re-parsed.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional

from .parser import Entry

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
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_kind        ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_group       ON items(kind, group_title);
CREATE INDEX IF NOT EXISTS idx_items_series      ON items(series_key);
CREATE INDEX IF NOT EXISTS idx_items_name        ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_hash        ON items(hash);

CREATE TABLE IF NOT EXISTS imported (
    hash        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- scan -------------------------------------------------------------
    def replace_items(self, entries: Iterable[Entry]) -> int:
        cur = self._conn.cursor()
        cur.execute("DELETE FROM items")
        count = 0
        batch: list[tuple] = []
        for e in entries:
            batch.append((
                e.kind, e.name, e.group_title, e.tvg_id, e.tvg_logo,
                e.url, e.extinf, e.series_key, e.series_name,
                e.season, e.episode, e.hash,
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

    @staticmethod
    def _insert_batch(cur, batch):
        cur.executemany(
            """INSERT INTO items
               (kind,name,group_title,tvg_id,tvg_logo,url,extinf,
                series_key,series_name,season,episode,hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )

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

    # --- queries ----------------------------------------------------------
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
        where = ["kind='series'"]
        args: list = []
        if group:
            where.append("group_title=?")
            args.append(group)
        if q:
            where.append("series_name LIKE ?")
            args.append(f"%{q}%")
        clause = " AND ".join(where)
        total = self._conn.execute(
            f"""SELECT COUNT(*) n FROM (
                    SELECT series_key FROM items WHERE {clause}
                    GROUP BY series_key)""",
            args,
        ).fetchone()["n"]
        rows = self._conn.execute(
            f"""SELECT series_key, MIN(series_name) name, MIN(group_title) grp,
                       COUNT(*) episodes, COUNT(DISTINCT season) seasons
                FROM items WHERE {clause}
                GROUP BY series_key ORDER BY name LIMIT ? OFFSET ?""",
            (*args, limit, offset),
        ).fetchall()
        out = [{
            "series_key": r["series_key"], "name": r["name"], "group": r["grp"],
            "episodes": r["episodes"], "seasons": r["seasons"],
        } for r in rows]
        return out, total

    def series_detail(self, series_key: str) -> dict:
        rows = self._conn.execute(
            """SELECT id,name,season,episode,hash FROM items
               WHERE kind='series' AND series_key=?
               ORDER BY season, episode, name""",
            (series_key,),
        ).fetchall()
        imported = self._imported_set([r["hash"] for r in rows])
        seasons: dict[int, list] = {}
        for r in rows:
            s = r["season"] if r["season"] is not None else 0
            seasons.setdefault(s, []).append({
                "id": r["id"], "name": r["name"],
                "season": r["season"], "episode": r["episode"],
                "imported": r["hash"] in imported,
            })
        return {
            "series_key": series_key,
            "seasons": [
                {"season": s, "episodes": eps}
                for s, eps in sorted(seasons.items())
            ],
        }

    def rows_for_ids(self, ids: list[int]) -> list[sqlite3.Row]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self._conn.execute(
            f"SELECT id,kind,name,extinf,url,hash FROM items WHERE id IN ({marks})",
            ids,
        ).fetchall()

    def rows_for_series(self, series_key: str, season: Optional[int]) -> list[sqlite3.Row]:
        if season is None:
            return self._conn.execute(
                "SELECT id,kind,name,extinf,url,hash FROM items "
                "WHERE kind='series' AND series_key=? ORDER BY season,episode",
                (series_key,),
            ).fetchall()
        return self._conn.execute(
            "SELECT id,kind,name,extinf,url,hash FROM items "
            "WHERE kind='series' AND series_key=? AND season=? ORDER BY episode",
            (series_key, season),
        ).fetchall()

    # --- imported ledger --------------------------------------------------
    def _imported_set(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        marks = ",".join("?" * len(hashes))
        rows = self._conn.execute(
            f"SELECT hash FROM imported WHERE hash IN ({marks})", hashes
        ).fetchall()
        return {r["hash"] for r in rows}

    def mark_imported(self, records: list[tuple[str, str, str]]) -> int:
        """records: (hash, kind, name). Returns number newly inserted."""
        cur = self._conn.cursor()
        before = cur.execute("SELECT COUNT(*) n FROM imported").fetchone()["n"]
        cur.executemany(
            "INSERT OR IGNORE INTO imported(hash,kind,name) VALUES(?,?,?)",
            records,
        )
        self._conn.commit()
        after = cur.execute("SELECT COUNT(*) n FROM imported").fetchone()["n"]
        return after - before

    def is_imported(self, hashes: Iterable[str]) -> set[str]:
        return self._imported_set(list(hashes))

    def imported_list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT hash,kind,name,imported_at FROM imported ORDER BY imported_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
