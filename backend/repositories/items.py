"""Scan cache (the `items` table): rebuilt on every sync, one row per
live channel / movie / series. Pure data access — no business logic, no
cross-table joins into the ledger (the service layer composes those)."""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from ..db import Database

PICK_COLS = ("id,kind,name,group_title,url,extinf,series_key,series_name,"
             "season,episode,tmdb,provider_id,hash,metadata")


class ItemsRepo:
    def __init__(self, db: Database):
        self._conn = db.conn

    # --- ingest -----------------------------------------------------------
    def replace_all(self, rows: Iterable[dict]) -> int:
        """Rebuild the scan cache from Xtream-derived dict rows."""
        cur = self._conn.cursor()
        cur.execute("DELETE FROM items")
        count = 0
        batch: list[tuple] = []
        for r in rows:
            batch.append((
                r["kind"], r["name"], r.get("group_title", ""), "",
                r.get("tvg_logo", ""), r.get("url", ""), "",
                r.get("series_key", ""), r.get("series_name", ""),
                r.get("season"), r.get("episode"), r.get("tmdb", ""),
                r.get("provider_id", ""), r["hash"], r.get("metadata", ""),
            ))
            if len(batch) >= 5000:
                self._insert_batch(cur, batch)
                count += len(batch)
                batch.clear()
        if batch:
            self._insert_batch(cur, batch)
            count += len(batch)
        self._conn.commit()
        return count

    @staticmethod
    def _insert_batch(cur, batch) -> None:
        cur.executemany(
            """INSERT INTO items
               (kind,name,group_title,tvg_id,tvg_logo,url,extinf,
                series_key,series_name,season,episode,tmdb,provider_id,hash,metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )

    # --- browse -----------------------------------------------------------
    def kind_counts(self) -> dict:
        rows = self._conn.execute(
            "SELECT kind, COUNT(*) n FROM items GROUP BY kind"
        ).fetchall()
        out = {"live": 0, "movie": 0, "series": 0}
        for r in rows:
            out[r["kind"]] = r["n"]
        return out

    def groups(self, kind: str) -> list[dict]:
        if kind == "series":
            rows = self._conn.execute(
                "SELECT group_title AS name, COUNT(DISTINCT series_key) AS n "
                "FROM items WHERE kind='series' GROUP BY group_title "
                "ORDER BY group_title"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT group_title AS name, COUNT(*) AS n FROM items "
                "WHERE kind=? GROUP BY group_title ORDER BY group_title", (kind,)
            ).fetchall()
        return [{"name": r["name"], "count": r["n"]} for r in rows]

    def page(self, kind: str, group: Optional[str], q: Optional[str],
             limit: int, offset: int) -> tuple[list[sqlite3.Row], int]:
        where, args = ["kind=?"], [kind]
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
            f"SELECT id,name,group_title,hash FROM items WHERE {clause} "
            f"ORDER BY name LIMIT ? OFFSET ?", (*args, limit, offset),
        ).fetchall()
        return rows, total

    def series_page(self, group: Optional[str], q: Optional[str],
                    limit: int, offset: int) -> tuple[list[sqlite3.Row], int]:
        where, args = ["kind='series'"], []
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
            f"SELECT series_key, name, group_title, total_seasons, total_episodes "
            f"FROM items WHERE {clause} ORDER BY name LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return rows, total

    def series_row(self, series_key: str) -> Optional[sqlite3.Row]:
        """Scan-cache row for a series (name/group/tmdb) to label lazily-fetched
        episodes."""
        return self._conn.execute(
            "SELECT name, group_title, tmdb, metadata FROM items "
            "WHERE kind='series' AND series_key=? LIMIT 1", (series_key,)
        ).fetchone()

    def rows_for_ids(self, ids: list[int]) -> list[sqlite3.Row]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self._conn.execute(
            f"SELECT {PICK_COLS} FROM items WHERE id IN ({marks})", ids
        ).fetchall()

    # --- cached series totals (lazy) --------------------------------------
    def cached_series_totals(self, keys: list[str]) -> dict[str, dict]:
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        rows = self._conn.execute(
            f"SELECT series_key, total_seasons, total_episodes FROM items "
            f"WHERE kind='series' AND series_key IN ({marks}) "
            f"AND total_seasons IS NOT NULL", keys
        ).fetchall()
        return {r["series_key"]: {"seasons": r["total_seasons"], "episodes": r["total_episodes"]} for r in rows}

    def set_series_total(self, series_key: str, seasons: int, episodes: int) -> None:
        self._conn.execute(
            "UPDATE items SET total_seasons=?, total_episodes=? "
            "WHERE kind='series' AND series_key=?", (seasons, episodes, series_key)
        )
        self._conn.commit()

    # --- bulk M3U import matching -----------------------------------------
    def rows_by_provider_ids(self, kind: str, pids) -> list[sqlite3.Row]:
        """Full pick rows for live/movie items matching the given provider ids."""
        pids = list(pids)
        out: list[sqlite3.Row] = []
        for i in range(0, len(pids), 900):  # stay under SQLite's variable limit
            chunk = pids[i:i + 900]
            marks = ",".join("?" * len(chunk))
            out += self._conn.execute(
                f"SELECT {PICK_COLS} FROM items "
                f"WHERE kind=? AND provider_id IN ({marks})", (kind, *chunk)
            ).fetchall()
        return out

    def all_series(self) -> list[sqlite3.Row]:
        """Every series row (name/key/group/tmdb) — for matching by name."""
        return self._conn.execute(
            "SELECT series_key, name, group_title, tmdb, metadata FROM items WHERE kind='series'"
        ).fetchall()
