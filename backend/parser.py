"""Streaming M3U parser + classification + series grouping.

Reads an M3U line by line (low memory, handles very large files) and yields
normalised entries. Each entry keeps its raw #EXTINF line and URL verbatim so
that import is a byte-faithful copy of the source.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

# tvg-* / group-title attributes inside an #EXTINF line: key="value"
_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')

# Episode markers, most specific first.
_SXXEXX_RE = re.compile(r'(?i)\bS\s*(\d{1,3})\s*[ .]?\s*E\s*(\d{1,4})\b')
_SEASON_EP_RE = re.compile(r'(?i)\bseason\s*(\d{1,3})\b.*?\bepisode\s*(\d{1,4})\b')
_NxNN_RE = re.compile(r'\b(\d{1,3})x(\d{1,4})\b')

# Movie group-title hint: last resort for flat catalogues that omit a /movie/
# URL segment. There is intentionally no series hint (see classify()).
_MOVIE_HINTS = ("vod", "movie", "movies", "film", "filme", "peli")


@dataclass
class Entry:
    name: str
    url: str
    extinf: str  # raw #EXTINF line, verbatim
    group_title: str = ""
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    kind: str = "live"  # live | movie | series
    series_name: str = ""
    series_key: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None
    hash: str = field(default="")

    def compute_hash(self) -> str:
        h = hashlib.sha1()
        h.update(self.extinf.encode("utf-8", "replace"))
        h.update(b"\n")
        h.update(self.url.encode("utf-8", "replace"))
        return h.hexdigest()


def _parse_extinf(line: str) -> tuple[str, dict[str, str]]:
    """Return (display_name, attrs) from an #EXTINF line."""
    attrs = {k.lower(): v for k, v in _ATTR_RE.findall(line)}
    # Display name is everything after the last comma that is not inside quotes.
    # In practice the trailing ',Name' is reliable.
    name = ""
    comma = line.rfind(",")
    if comma != -1:
        name = line[comma + 1:].strip()
    return name, attrs


def _strip_episode_token(name: str) -> str:
    """Remove episode markers and trailing junk to get a clean series title."""
    for rx in (_SXXEXX_RE, _SEASON_EP_RE, _NxNN_RE):
        m = rx.search(name)
        if m:
            name = name[: m.start()]
            break
    # Trim trailing separators/quality tags left behind.
    name = re.sub(r'(?i)\b(season|temporada|sezon)\s*\d{0,3}\s*$', "", name)
    name = name.strip(" -_:|.–—\t")
    return name or "Unknown"


def _extract_episode(name: str) -> tuple[Optional[int], Optional[int]]:
    for rx in (_SXXEXX_RE, _SEASON_EP_RE):
        m = rx.search(name)
        if m:
            return int(m.group(1)), int(m.group(2))
    m = _NxNN_RE.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _series_key(series_name: str, group_title: str) -> str:
    """Stable, case/space-insensitive key. Group is included so identically
    named series from different categories don't merge. Numbers are preserved
    (so 'Spectrum News 1' and 'Spectrum News 13' never collapse)."""
    base = re.sub(r"\s+", " ", series_name.strip().lower())
    grp = re.sub(r"\s+", " ", (group_title or "").strip().lower())
    return f"{grp}␟{base}"


def classify(url: str, group_title: str, name: str) -> str:
    u = url.lower()
    # URL path segment is the most reliable signal for XC-style flat M3Us and is
    # authoritative: a 24/7 "live" channel can sit in a group named "TV SHOWS",
    # so group-title keywords are NOT trusted for type.
    if "/series/" in u:
        return "series"
    if "/movie/" in u or "/movies/" in u:
        return "movie"
    if "/live/" in u:
        return "live"
    # Flat providers without type segments: an episode marker in the title is a
    # strong series signal. A group-title series keyword is deliberately NOT used
    # as a fallback — live channels are routinely filed under groups named
    # "TV SHOWS"/"SERIES", which would misclassify them. Real series carry either
    # a /series/ URL or an SxxExx marker. A movie keyword is kept as a low-risk
    # last resort for flat catalogues that omit /movie/.
    if _SXXEXX_RE.search(name) or _SEASON_EP_RE.search(name) or _NxNN_RE.search(name):
        return "series"
    g = (group_title or "").lower()
    if any(k in g for k in _MOVIE_HINTS):
        return "movie"
    return "live"


def _finish(entry: Entry) -> Entry:
    entry.kind = classify(entry.url, entry.group_title, entry.name)
    if entry.kind == "series":
        entry.season, entry.episode = _extract_episode(entry.name)
        entry.series_name = _strip_episode_token(entry.name)
        entry.series_key = _series_key(entry.series_name, entry.group_title)
    entry.hash = entry.compute_hash()
    return entry


def parse(path: str) -> Iterator[Entry]:
    """Yield Entry objects from an M3U file. Streams; constant memory."""
    pending: Optional[str] = None  # the most recent #EXTINF line
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            if not line:
                continue
            if line.startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF"):
                pending = line
                continue
            if line.startswith("#"):
                # Other directives (#EXTGRP, #KODIPROP, ...) — ignore for v1.
                continue
            # A non-# line is a URL; pair it with the pending #EXTINF.
            if pending is None:
                continue
            name, attrs = _parse_extinf(pending)
            entry = Entry(
                name=name or attrs.get("tvg-name", "") or "Untitled",
                url=line,
                extinf=pending,
                group_title=attrs.get("group-title", ""),
                tvg_id=attrs.get("tvg-id", ""),
                tvg_name=attrs.get("tvg-name", ""),
                tvg_logo=attrs.get("tvg-logo", ""),
            )
            pending = None
            yield _finish(entry)
