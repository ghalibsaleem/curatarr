"""The curated ledger (the `imported` table): the source of truth for what we
expose to Dispatcharr. Stores each pick's full data keyed by a content hash.
Includes the read queries that back the Xtream wrapper."""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional
from urllib.parse import urlsplit

from ..db import Database


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


class LedgerRepo:
    def __init__(self, db: Database):
        self._conn = db.conn

    # --- membership / counts ---------------------------------------------
    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) n FROM imported").fetchone()["n"]

    def imported_hashes(self, hashes: Iterable[str]) -> set[str]:
        hl = list(hashes)
        if not hl:
            return set()
        marks = ",".join("?" * len(hl))
        rows = self._conn.execute(
            f"SELECT hash FROM imported WHERE hash IN ({marks})", hl
        ).fetchall()
        return {r["hash"] for r in rows}

    def imported_series_counts(self, keys: list[str]) -> dict[str, dict]:
        """Imported episode/season counts per series_key (only keys present)."""
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        rows = self._conn.execute(
            f"SELECT series_key, COUNT(*) eps, COUNT(DISTINCT season) seasons "
            f"FROM imported WHERE kind='series' AND series_key IN ({marks}) "
            f"GROUP BY series_key", keys
        ).fetchall()
        return {r["series_key"]: {"episodes": r["eps"], "seasons": r["seasons"]} for r in rows}

    # --- writes -----------------------------------------------------------
    def mark(self, rows, source: str = "app") -> int:
        """Insert full pick records (idempotent by hash). Returns newly inserted."""
        recs = [(
            r["hash"], r["kind"], r["name"], r["group_title"], r["url"],
            r["extinf"], r["series_key"], r["series_name"],
            r["season"], r["episode"], container_ext(r["url"], r["kind"]),
            r["tmdb"], r["provider_id"], source,
        ) for r in rows]
        cur = self._conn.cursor()
        before = self.count()
        cur.executemany(
            """INSERT OR IGNORE INTO imported
               (hash,kind,name,group_title,url,extinf,series_key,series_name,
                season,episode,container_extension,tmdb,provider_id,source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            recs,
        )
        self._conn.commit()
        return self.count() - before

    def remove(self, ids: list[int]) -> int:
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = self._conn.cursor()
        cur.execute(f"DELETE FROM imported WHERE id IN ({marks})", ids)
        self._conn.commit()
        return cur.rowcount

    def list_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id,kind,name,group_title,series_key,series_name,"
            "season,episode,imported_at FROM imported ORDER BY imported_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def backfill_from_items(self) -> None:
        """Fill tmdb/provider_id on existing picks so items imported before these
        columns existed gain them without re-import. tmdb comes from the rebuilt
        scan cache (by hash); provider_id is parsed from the stored stream URL."""
        self._conn.execute(
            "UPDATE imported SET tmdb = COALESCE("
            "  (SELECT i.tmdb FROM items i WHERE i.hash=imported.hash), tmdb) "
            "WHERE tmdb=''"
        )
        rows = self._conn.execute(
            "SELECT id, url FROM imported WHERE provider_id='' AND url!=''"
        ).fetchall()
        for r in rows:
            last = urlsplit(r["url"]).path.rsplit("/", 1)[-1]
            pid = last.rsplit(".", 1)[0] if "." in last else last
            if pid:
                self._conn.execute(
                    "UPDATE imported SET provider_id=? WHERE id=?", (pid, r["id"])
                )
        self._conn.commit()

    # --- Xtream wrapper reads --------------------------------------------
    def categories(self, kind: str) -> list[dict]:
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

    def streams(self, kind: str, group: Optional[str]) -> list[sqlite3.Row]:
        sql = ("SELECT id,name,group_title,url,container_extension,tmdb "
               "FROM imported WHERE kind=?")
        args: list = [kind]
        if group is not None:
            sql += " AND group_title=?"
            args.append(group)
        sql += " ORDER BY name"
        return self._conn.execute(sql, args).fetchall()

    def series(self, group: Optional[str]) -> list[sqlite3.Row]:
        sql = ("SELECT series_key, MIN(series_name) AS name, MIN(group_title) AS grp, "
               "MAX(tmdb) AS tmdb, COUNT(*) AS episodes FROM imported WHERE kind='series'")
        args: list = []
        if group is not None:
            sql += " AND group_title=?"
            args.append(group)
        sql += " GROUP BY series_key ORDER BY name"
        return self._conn.execute(sql, args).fetchall()

    def series_keys(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT series_key FROM imported WHERE kind='series'"
        ).fetchall()
        return [r["series_key"] for r in rows]

    def series_episodes(self, series_key: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT id,name,series_name,season,episode,container_extension,url,tmdb "
            "FROM imported WHERE kind='series' AND series_key=? "
            "ORDER BY season,episode", (series_key,)
        ).fetchall()

    def url(self, stream_id: int) -> Optional[str]:
        row = self._conn.execute(
            "SELECT url FROM imported WHERE id=?", (stream_id,)
        ).fetchone()
        return row["url"] if row else None

    def stream_ref(self, stream_id: int) -> Optional[sqlite3.Row]:
        """Creds-free reference (kind, provider_id, container_extension) for
        rebuilding a redirect URL against any subscription."""
        return self._conn.execute(
            "SELECT kind, provider_id, container_extension FROM imported WHERE id=?",
            (stream_id,),
        ).fetchone()
