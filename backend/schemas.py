"""Pydantic request models for the API layer."""
from __future__ import annotations

from typing import Literal

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


class DispatcharrConfig(BaseModel):
    url: str = ""
    username: str = ""
    password: str = ""
    # One XC account per Curatarr subscription (load-balancing) → refresh each.
    account_ids: list[int] = []
    account_names: list[str] = []


class JellyfinConfig(BaseModel):
    url: str = ""
    api_key: str = ""
    task_id: str = ""
    task_name: str = ""
    library_ids: list[str] = []
    library_names: list[str] = []


class DownstreamConfig(BaseModel):
    dispatcharr: DispatcharrConfig | None = None
    jellyfin: JellyfinConfig | None = None


class DiscoverDispatcharr(BaseModel):
    url: str
    username: str
    password: str


class DiscoverJellyfin(BaseModel):
    url: str
    api_key: str


class AuthCredentials(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


class ScheduleConfig(BaseModel):
    enabled: bool = False
    frequency: Literal["daily", "twice-weekly", "weekly", "monthly"] = "daily"
    time: str = "03:00"                  # HH:MM, in `tz`
    days: list[int] = [0]               # cron weekday(s): 0=Sun … 6=Sat
    day_of_month: int = 1               # used by the monthly frequency
    tz: str = "UTC"                     # IANA timezone name
    downstream: bool = False            # chain the downstream sync after each run
