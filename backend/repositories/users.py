"""Users and session tokens for UI authentication. Pure data access."""
from __future__ import annotations

import sqlite3
from typing import Optional

from ..db import Database


class UsersRepo:
    def __init__(self, db: Database):
        self._conn = db.conn

    # --- users ------------------------------------------------------------
    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]

    def by_username(self, username: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT id, username, pw_hash, is_admin FROM users WHERE username=?",
            (username,),
        ).fetchone()

    def by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT id, username, is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()

    def create(self, username: str, pw_hash: str, is_admin: bool = True) -> int:
        cur = self._conn.execute(
            "INSERT INTO users(username, pw_hash, is_admin) VALUES(?,?,?)",
            (username, pw_hash, 1 if is_admin else 0),
        )
        self._conn.commit()
        return cur.lastrowid

    def set_password(self, user_id: int, pw_hash: str) -> None:
        self._conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash, user_id))
        self._conn.commit()

    # --- sessions ---------------------------------------------------------
    def create_session(self, token: str, user_id: int, expires_at: str) -> None:
        self._conn.execute(
            "INSERT INTO sessions(token, user_id, expires_at) VALUES(?,?,?)",
            (token, user_id, expires_at),
        )
        self._conn.commit()

    def session_user(self, token: str) -> Optional[sqlite3.Row]:
        """User row for a non-expired session token, or None."""
        return self._conn.execute(
            "SELECT u.id, u.username, u.is_admin FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token=? AND s.expires_at > datetime('now')",
            (token,),
        ).fetchone()

    def delete_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        self._conn.commit()

    def purge_expired(self) -> None:
        self._conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
        self._conn.commit()
