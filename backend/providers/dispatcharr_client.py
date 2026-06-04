"""Client for *Dispatcharr's* REST API (a downstream target).

Used to trigger a full refresh of the curated Xtream ("M3U") account after we
curate, then wait for it to finish before kicking off Jellyfin. Endpoints and the
pollable `status` field were verified against Dispatcharr v0.25.1 source:
  - POST /api/accounts/token/                 -> {access, refresh}  (JWT)
  - GET  /api/m3u/accounts/                    -> list (id, name, server_url, status, account_type)
  - POST /api/m3u/refresh/{account_id}/        -> 202 (full account refresh pipeline)
Stdlib only (urllib) to avoid new dependencies.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Optional

# M3UAccount.Status values that mean a refresh is still running.
_BUSY = {"fetching", "parsing"}


class DispatcharrError(Exception):
    pass


class DispatcharrClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._token: Optional[str] = None

    # --- transport --------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 auth: bool = True) -> Optional[dict]:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "curatarr/1"}
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token()}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise DispatcharrError(f"Dispatcharr {method} {path} -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise DispatcharrError(f"Cannot reach Dispatcharr at {self.base}: {e.reason}")
        if not raw:
            return None
        return json.loads(raw.decode("utf-8", "replace"))

    def _access_token(self) -> str:
        if self._token:
            return self._token
        out = self._request(
            "POST", "/api/accounts/token/",
            {"username": self.username, "password": self.password}, auth=False,
        ) or {}
        token = out.get("access")
        if not token:
            raise DispatcharrError("Dispatcharr login failed: no access token returned")
        self._token = token
        return token

    # --- operations -------------------------------------------------------
    def accounts(self) -> list[dict]:
        """All M3U accounts (the API may paginate; handle list or {results}}."""
        out = self._request("GET", "/api/m3u/accounts/")
        if isinstance(out, dict):
            out = out.get("results", [])
        return out or []

    def find_account(self, server_url: str) -> Optional[dict]:
        """Match our curated XC account by server URL (host[:port] match)."""
        want = urllib.parse.urlsplit(server_url).netloc.lower()
        for a in self.accounts():
            su = (a.get("server_url") or "")
            if want and want == urllib.parse.urlsplit(su).netloc.lower():
                return a
        return None

    def refresh_account(self, account_id: int) -> None:
        self._request("POST", f"/api/m3u/refresh/{account_id}/")

    def account_status(self, account_id: int) -> str:
        out = self._request("GET", f"/api/m3u/accounts/{account_id}/") or {}
        return (out.get("status") or "").lower()

    def wait_until_done(self, account_id: int, timeout: int = 600,
                        interval: int = 2, settle_grace: int = 10) -> str:
        """Poll until the account refresh finishes; return the final status
        ('success' / 'error' / 'idle' / ...).

        The refresh task is async, so the account may still read a settled
        status for a moment after we trigger it. We wait up to `settle_grace`
        seconds for it to enter a busy state; if it never does, we assume the
        refresh completed quickly and return the current status."""
        start = time.time()
        deadline = start + timeout
        seen_busy = False
        while time.time() < deadline:
            st = self.account_status(account_id)
            if st in _BUSY:
                seen_busy = True
            elif seen_busy:
                return st                       # busy -> settled = finished
            elif st == "error":
                return st
            elif time.time() - start > settle_grace:
                return st                       # never went busy = finished fast
            time.sleep(interval)
        raise DispatcharrError(f"Timed out waiting for Dispatcharr refresh ({timeout}s)")
