"""UI authentication: a single admin account created on first run, with
DB-backed opaque session tokens. Passwords are hashed with bcrypt.

This is separate from the XC_USER/XC_PASS wrapper credentials that Dispatcharr
uses to consume the public Xtream surface.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from ..errors import ConfigError
from ..repositories.users import UsersRepo

SESSION_TTL_DAYS = 30
COOKIE_NAME = "curatarr_session"
_BCRYPT_MAX = 72  # bcrypt only considers the first 72 bytes


def _hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:_BCRYPT_MAX], bcrypt.gensalt()).decode("utf-8")


def _verify_pw(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX], pw_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(self, users: UsersRepo):
        self.users = users

    def needs_setup(self) -> bool:
        """True when no account exists yet (first run → show setup, not login)."""
        return self.users.count() == 0

    def setup(self, username: str, password: str) -> str:
        """Create the first (admin) account and return a fresh session token.
        Refuses once any account exists."""
        if not self.needs_setup():
            raise ConfigError("An account already exists")
        username = (username or "").strip()
        if not username:
            raise ConfigError("Username is required")
        if len(password or "") < 8:
            raise ConfigError("Password must be at least 8 characters")
        uid = self.users.create(username, _hash_pw(password), is_admin=True)
        return self._mint(uid)

    def login(self, username: str, password: str) -> str | None:
        """Return a session token for valid credentials, else None."""
        row = self.users.by_username((username or "").strip())
        if not row or not _verify_pw(password or "", row["pw_hash"]):
            return None
        return self._mint(row["id"])

    def logout(self, token: str) -> None:
        if token:
            self.users.delete_session(token)

    def change_password(self, username: str, current: str, new: str) -> None:
        """Verify the current password and set a new one (≥ 8 chars)."""
        row = self.users.by_username((username or "").strip())
        if not row or not _verify_pw(current or "", row["pw_hash"]):
            raise ConfigError("Current password is incorrect")
        if len(new or "") < 8:
            raise ConfigError("New password must be at least 8 characters")
        self.users.set_password(row["id"], _hash_pw(new))

    def user_for_token(self, token: str | None):
        """User row for a valid, non-expired session token, or None."""
        if not token:
            return None
        return self.users.session_user(token)

    def _mint(self, user_id: int) -> str:
        self.users.purge_expired()
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
        self.users.create_session(token, user_id, expires.isoformat(timespec="seconds"))
        return token
