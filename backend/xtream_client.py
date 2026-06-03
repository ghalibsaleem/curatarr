"""Client for the *provider's* Xtream Codes API (the input side).

Replaces parsing the giant flat M3U: we read categories, movies, series and
(lazily) episodes straight from the provider's player_api.php. Credentials and
base URL are derived from the saved source URL (the get.php?...m3u_plus link
already contains username/password), so the user configures nothing extra.

Stdlib only (urllib) to avoid new dependencies.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from typing import Optional


def stream_hash(url: str) -> str:
    """Stable identity for a stream (idempotency/dedup key). The provider URL
    embeds the stream id, so it uniquely identifies the item."""
    return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()


_KIND_SEG = {"movie": "movie", "series": "series", "live": "live"}


def stream_url(base: str, username: str, password: str, kind: str,
               provider_id: str, ext: str) -> str:
    """Build a provider stream URL for a given subscription's credentials.
    Lets one curated pick (identified by provider_id) be served from any sub."""
    seg = _KIND_SEG.get(kind, "movie")
    e = ext or ("ts" if kind == "live" else "mp4")
    return f"{base.rstrip('/')}/{seg}/{username}/{password}/{provider_id}.{e}"


def creds_from_source(source_url: str) -> tuple[str, str, str]:
    """Return (base_url, username, password) parsed from a get.php / player_api
    / panel URL. base_url is scheme://host[:port] with no path."""
    p = urllib.parse.urlsplit(source_url)
    base = f"{p.scheme}://{p.netloc}"
    q = urllib.parse.parse_qs(p.query)
    user = (q.get("username") or [""])[0]
    pwd = (q.get("password") or [""])[0]
    return base, user, pwd


class ProviderXC:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout

    def _get(self, action: Optional[str] = None, **extra):
        params = {"username": self.username, "password": self.password}
        if action:
            params["action"] = action
        params.update({k: v for k, v in extra.items() if v is not None})
        url = f"{self.base}/player_api.php?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "m3u-curator/1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8", "replace"))

    # --- metadata ---------------------------------------------------------
    def authenticate(self) -> dict:
        info = self._get()
        if not isinstance(info, dict) or not info.get("user_info"):
            raise ValueError("Xtream auth failed: no user_info in response")
        if info["user_info"].get("auth") == 0:
            raise ValueError("Xtream auth failed: invalid credentials")
        return info

    def live_categories(self):
        return self._get("get_live_categories")

    def live_streams(self, category_id=None):
        return self._get("get_live_streams", category_id=category_id)

    def vod_categories(self):
        return self._get("get_vod_categories")

    def vod_streams(self, category_id=None):
        return self._get("get_vod_streams", category_id=category_id)

    def series_categories(self):
        return self._get("get_series_categories")

    def series(self, category_id=None):
        return self._get("get_series", category_id=category_id)

    def series_info(self, series_id):
        return self._get("get_series_info", series_id=series_id)

    # --- stream URLs (what we store / redirect to) ------------------------
    def live_url(self, stream_id, ext="ts"):
        return f"{self.base}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    def movie_url(self, stream_id, ext="mp4"):
        return f"{self.base}/movie/{self.username}/{self.password}/{stream_id}.{ext}"

    def episode_url(self, stream_id, ext="mp4"):
        return f"{self.base}/series/{self.username}/{self.password}/{stream_id}.{ext}"
