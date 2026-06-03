"""Pydantic request models for the API layer."""
from __future__ import annotations

from pydantic import BaseModel


class Sub(BaseModel):
    url: str
    username: str
    password: str


class SourceRequest(BaseModel):
    subs: list[Sub]


class ImportRequest(BaseModel):
    ids: list[int] | None = None          # live/movie scan-cache ids
    series_key: str | None = None         # provider series id
    season: int | None = None             # with series_key: just this season
    episode_ids: list[str] | None = None  # with series_key: specific episodes


class UnimportRequest(BaseModel):
    ids: list[int]  # ledger row ids (from /api/imported)


class CountsRequest(BaseModel):
    series_keys: list[str]
