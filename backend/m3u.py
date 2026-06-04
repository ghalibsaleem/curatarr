"""Minimal M3U parser for bulk-importing an existing curated playlist.

We don't trust the URLs' credentials (the playlist may come from a different
subscription of the same provider); we only need each entry's name, group, and
the provider stream id embedded in the URL — which is identical across subs of
the same panel.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_SXXEXX_RE = re.compile(r"(?i)\bS\s*(\d{1,3})\s*[ .]?\s*E\s*(\d{1,4})\b")


def parse(text: str):
    """Yield {name, group, url} for each entry in an M3U document."""
    name, group = "", ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = {k.lower(): v for k, v in _ATTR_RE.findall(line)}
            group = attrs.get("group-title", "")
            comma = line.rfind(",")
            name = (line[comma + 1:].strip() if comma != -1 else "") or attrs.get("tvg-name", "")
        elif line.startswith("#"):
            continue
        else:
            yield {"name": name, "group": group, "url": line}
            name, group = "", ""


def classify_url(url: str) -> tuple[str, str, str]:
    """Return (kind, provider_id, ext) from a stream URL.

    Handles both `/movie|series|live/user/pass/{id}.ext` and the bare
    `/{account}/{token}/{id}` live form — provider_id is the last path segment
    without its extension.
    """
    path = urlsplit(url).path
    last = path.rsplit("/", 1)[-1]
    if "." in last:
        pid, ext = last.rsplit(".", 1)
    else:
        pid, ext = last, ""
    low = path.lower()
    if "/series/" in low:
        kind = "series"
    elif "/movie/" in low or "/movies/" in low:
        kind = "movie"
    else:
        kind = "live"  # explicit /live/ or the bare account/token/id form
    return kind, pid, ext


def split_episode(name: str) -> tuple[int | None, int | None, str]:
    """(season, episode, series_name) parsed from an episode title; season/episode
    are None when no SxxExx marker is present."""
    m = _SXXEXX_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2)), name[: m.start()].strip(" -_:|.\t")
    return None, None, name.strip()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())
