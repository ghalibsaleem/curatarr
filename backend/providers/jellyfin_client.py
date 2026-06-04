"""Client for *Jellyfin's* REST API (a downstream target).

After Dispatcharr re-scans the curated catalogue, we (1) run the Xtream-library
plugin's "sync now" scheduled task and (2) trigger a metadata refresh for only
the specific libraries the user picked (saves compute vs. a full server scan).

  - GET  /ScheduledTasks                       -> tasks (Id, Name, State)
  - POST /ScheduledTasks/Running/{taskId}      -> start a task
  - GET  /ScheduledTasks/{taskId}              -> {State, LastExecutionResult}
  - GET  /Library/VirtualFolders               -> libraries (Name, ItemId, ...)
  - POST /Items/{itemId}/Refresh?...           -> refresh one library
Auth via the X-Emby-Token header. Stdlib only (urllib).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Optional


class JellyfinError(Exception):
    pass


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # --- transport --------------------------------------------------------
    def _request(self, method: str, path: str, query: Optional[dict] = None):
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "X-Emby-Token": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "curatarr/1",
        }
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise JellyfinError(f"Jellyfin {method} {path} -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise JellyfinError(f"Cannot reach Jellyfin at {self.base}: {e.reason}")
        if not raw:
            return None
        return json.loads(raw.decode("utf-8", "replace"))

    # --- scheduled tasks (plugin sync) ------------------------------------
    def tasks(self) -> list[dict]:
        return self._request("GET", "/ScheduledTasks") or []

    def find_task(self, name_contains: str) -> Optional[dict]:
        needle = name_contains.lower()
        for t in self.tasks():
            if needle in (t.get("Name") or "").lower():
                return t
        return None

    def run_task(self, task_id: str) -> None:
        self._request("POST", f"/ScheduledTasks/Running/{task_id}")

    def task_state(self, task_id: str) -> str:
        out = self._request("GET", f"/ScheduledTasks/{task_id}") or {}
        return out.get("State") or ""

    def wait_task(self, task_id: str, timeout: int = 1800, interval: int = 3,
                  settle_grace: int = 10) -> str:
        """Poll a scheduled task until it returns to Idle. Mirrors the
        Dispatcharr grace logic: wait briefly for it to start running."""
        start = time.time()
        deadline = start + timeout
        seen_running = False
        while time.time() < deadline:
            st = self.task_state(task_id)
            if st and st != "Idle":
                seen_running = True
            elif seen_running:
                return st or "Idle"             # running -> Idle = finished
            elif time.time() - start > settle_grace:
                return st or "Idle"             # never ran = finished fast
            time.sleep(interval)
        raise JellyfinError(f"Timed out waiting for Jellyfin task ({timeout}s)")

    # --- libraries --------------------------------------------------------
    def libraries(self) -> list[dict]:
        """Virtual folders: each has Name, ItemId, CollectionType, Locations."""
        return self._request("GET", "/Library/VirtualFolders") or []

    def refresh_library(self, item_id: str) -> None:
        """Targeted metadata refresh for a single library (not the whole server)."""
        self._request("POST", f"/Items/{item_id}/Refresh", {
            "Recursive": "true",
            "ImageRefreshMode": "Default",
            "MetadataRefreshMode": "Default",
        })
