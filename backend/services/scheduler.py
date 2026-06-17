"""Scheduled auto-sync: an in-process asyncio loop that runs the provider sync
(and optionally the downstream sync) on a cron schedule.

The schedule is configured in the UI as a friendly frequency + time-of-day in a
chosen timezone; we translate it to a standard cron expression and use croniter
to compute the next run. Cron semantics apply: a slot missed while the app was
down is skipped, and the next future slot is used.

Concurrency: sync.run() is blocking (~60s), so it runs in a worker thread via
asyncio.to_thread to keep the event loop responsive. SyncService holds its own
lock, so a scheduled run and a manual "Sync now" can never overlap.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

from croniter import croniter

from ..errors import ConfigError
from ..repositories.meta import MetaRepo
from .downstream import DownstreamService
from .sync import SyncService

_CONFIG_KEY = "schedule"
_RESULT_KEY = "last_auto_sync"

_FREQUENCIES = ("daily", "twice-weekly", "weekly", "monthly")

_DEFAULT: dict = {
    "enabled": False,
    "frequency": "daily",
    "time": "03:00",
    "days": [0],          # cron weekday(s): 0=Sun … 6=Sat (used by weekly/twice-weekly)
    "day_of_month": 1,    # used by monthly
    "tz": "UTC",
    "downstream": False,  # also chain downstream sync after the provider sync
}


def _now_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")


def _summarize_downstream(ds: dict) -> str:
    stages = ds.get("stages") or []
    ok = sum(1 for s in stages if s.get("status") == "ok")
    bad = [s["name"] for s in stages if s.get("status") == "error"]
    if bad:
        return f"{ok}/{len(stages)} ok; failed: {', '.join(bad)}"
    return f"{ok}/{len(stages)} stages ok"


class SchedulerService:
    def __init__(self, meta: MetaRepo, sync: SyncService, downstream: DownstreamService):
        self.meta = meta
        self.sync = sync
        self.downstream = downstream
        self._task: asyncio.Task | None = None
        self._loop_ref: asyncio.AbstractEventLoop | None = None
        self._wake = asyncio.Event()

    # ---- config -----------------------------------------------------------

    def get_config(self) -> dict:
        cfg = dict(_DEFAULT)
        raw = self.meta.get(_CONFIG_KEY)
        if raw:
            cfg.update(json.loads(raw))
        return cfg

    def set_config(self, cfg: dict) -> dict:
        merged = dict(_DEFAULT)
        merged.update(cfg)
        self._validate(merged)
        self.meta.set(_CONFIG_KEY, json.dumps(merged))
        self._wake_loop()  # recompute next run immediately
        return merged

    @staticmethod
    def _validate(cfg: dict) -> None:
        try:
            hh, mm = str(cfg["time"]).split(":")
            h, m = int(hh), int(mm)
            assert 0 <= h <= 23 and 0 <= m <= 59
        except Exception:
            raise ConfigError("Time must be HH:MM (00:00–23:59)")
        if cfg["frequency"] not in _FREQUENCIES:
            raise ConfigError("Invalid frequency")
        days = list(cfg.get("days") or [])
        if any(d < 0 or d > 6 for d in days):
            raise ConfigError("Day-of-week values must be 0 (Sun) – 6 (Sat)")
        if cfg["frequency"] == "weekly" and len(set(days)) != 1:
            raise ConfigError("Weekly needs exactly one day selected")
        if cfg["frequency"] == "twice-weekly" and len(set(days)) != 2:
            raise ConfigError("Twice-a-week needs exactly two days selected")
        if cfg["frequency"] == "monthly":
            dom = int(cfg.get("day_of_month") or 0)
            if not 1 <= dom <= 31:
                raise ConfigError("Day of month must be 1–31")
        if (cfg.get("tz") or "UTC") not in available_timezones():
            raise ConfigError(f"Unknown timezone: {cfg.get('tz')}")

    # ---- cron + next run --------------------------------------------------

    def _cron_expr(self, cfg: dict) -> str:
        hh, mm = str(cfg["time"]).split(":")
        minute, hour = int(mm), int(hh)
        freq = cfg["frequency"]
        if freq == "daily":
            return f"{minute} {hour} * * *"
        if freq == "weekly":
            return f"{minute} {hour} * * {(cfg.get('days') or [0])[0]}"
        if freq == "twice-weekly":
            days = ",".join(str(d) for d in sorted(set(cfg.get("days") or [0])))
            return f"{minute} {hour} * * {days}"
        if freq == "monthly":
            return f"{minute} {hour} {int(cfg.get('day_of_month') or 1)} * *"
        raise ConfigError(f"Unknown frequency: {freq}")

    def preview(self, cfg: dict) -> datetime | None:
        """Validate an unsaved config and return its next run (for the UI preview)."""
        merged = dict(_DEFAULT)
        merged.update(cfg)
        merged["enabled"] = True
        self._validate(merged)
        return self.next_run(merged)

    def next_run(self, cfg: dict | None = None,
                 after: datetime | None = None) -> datetime | None:
        cfg = cfg or self.get_config()
        if not cfg.get("enabled"):
            return None
        tz = ZoneInfo(cfg.get("tz") or "UTC")
        base = after or datetime.now(tz)
        if base.tzinfo is None:
            base = base.replace(tzinfo=tz)
        return croniter(self._cron_expr(cfg), base).get_next(datetime)

    # ---- status -----------------------------------------------------------

    def status(self) -> dict:
        cfg = self.get_config()
        try:
            nxt = self.next_run(cfg)
        except Exception:
            nxt = None
        raw = self.meta.get(_RESULT_KEY)
        return {
            "config": cfg,
            "next_sync": nxt.isoformat(timespec="seconds") if nxt else None,
            "last_result": json.loads(raw) if raw else None,
            "running": bool(self._task and not self._task.done()),
        }

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._loop_ref = asyncio.get_running_loop()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _wake_loop(self) -> None:
        """Wake the loop from another thread (config saved via a request)."""
        loop = self._loop_ref
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._wake.set)

    async def _wait_or_wake(self, timeout: float) -> bool:
        """Sleep up to `timeout` seconds; return True if woken by a config change."""
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(1.0, timeout))
            return True
        except asyncio.TimeoutError:
            return False

    async def _loop(self) -> None:
        last_fired: datetime | None = None
        while True:
            cfg = self.get_config()
            try:
                nxt = self.next_run(cfg, after=last_fired) if cfg.get("enabled") else None
            except Exception:
                nxt = None
            if nxt is None:
                last_fired = None
                await self._wait_or_wake(3600)  # disabled/invalid: wait for a change
                continue
            tz = ZoneInfo(cfg.get("tz") or "UTC")
            delay = (nxt - datetime.now(tz)).total_seconds()
            if delay > 0 and await self._wait_or_wake(delay):
                last_fired = None  # config changed mid-wait → recompute from now
                continue
            await self._run_once(cfg)
            last_fired = nxt

    async def _run_once(self, cfg: dict) -> None:
        result: dict = {"at": _now_iso(), "ok": True, "stages": []}
        try:
            count = await asyncio.to_thread(self.sync.run)
            result["stages"].append({"name": "Provider sync", "status": "ok",
                                     "message": f"{count} entries"})
        except Exception as e:  # noqa: BLE001 - record any failure for the UI
            result["ok"] = False
            result["stages"].append({"name": "Provider sync", "status": "error",
                                     "message": str(e)})
            self.meta.set(_RESULT_KEY, json.dumps(result))
            return

        if cfg.get("downstream"):
            try:
                ds = await asyncio.to_thread(self.downstream.run)
                if not ds.get("ok"):
                    result["ok"] = False
                result["stages"].append({
                    "name": "Downstream sync",
                    "status": "ok" if ds.get("ok") else "error",
                    "message": _summarize_downstream(ds),
                })
            except Exception as e:  # noqa: BLE001
                result["ok"] = False
                result["stages"].append({"name": "Downstream sync", "status": "error",
                                         "message": str(e)})

        self.meta.set(_RESULT_KEY, json.dumps(result))
