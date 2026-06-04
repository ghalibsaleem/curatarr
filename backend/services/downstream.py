"""Downstream sync: after curating, refresh the curated catalogue in Dispatcharr,
then run the Jellyfin Xtream-library plugin sync and refresh only the chosen
Jellyfin libraries — in sequence, from one action (issue #3).

Targeted by design: we refresh the *single* Dispatcharr M3U account and only the
*specific* Jellyfin libraries the user selected, rather than scanning everything.

Config is persisted as JSON in the `meta` table (same gitignored DB as the XC
credentials). Secrets are never logged or sent anywhere but the two targets.
"""
from __future__ import annotations

import json
from typing import Optional

from ..providers.dispatcharr_client import DispatcharrClient, DispatcharrError
from ..providers.jellyfin_client import JellyfinClient, JellyfinError
from ..repositories.meta import MetaRepo

_KEY = "downstream_config"

# Substring used to auto-detect the firestaerter Xtream-library plugin task.
_PLUGIN_TASK_HINT = "xtream"

_DEFAULT = {
    "dispatcharr": {"url": "", "username": "", "password": "",
                    "account_ids": [], "account_names": []},
    "jellyfin": {"url": "", "api_key": "", "task_id": "", "task_name": "",
                 "library_ids": [], "library_names": []},
}


def _merge(base: dict, over: dict) -> dict:
    out = {k: dict(v) for k, v in base.items()}
    for section, vals in (over or {}).items():
        if section in out and isinstance(vals, dict):
            out[section].update(vals)
    return out


class DownstreamService:
    def __init__(self, meta: MetaRepo):
        self.meta = meta

    # --- config -----------------------------------------------------------
    def get_config(self) -> dict:
        raw = self.meta.get(_KEY)
        return _merge(_DEFAULT, json.loads(raw)) if raw else _merge(_DEFAULT, {})

    def set_config(self, cfg: dict) -> dict:
        merged = _merge(self.get_config(), cfg)
        self.meta.set(_KEY, json.dumps(merged))
        return merged

    # --- discovery (uses creds from the request, pre-save) ----------------
    def dispatcharr_accounts(self, url: str, username: str, password: str) -> list[dict]:
        client = DispatcharrClient(url, username, password)
        return [
            {"id": a.get("id"), "name": a.get("name"),
             "server_url": a.get("server_url"), "type": a.get("account_type")}
            for a in client.accounts()
        ]

    def jellyfin_discover(self, url: str, api_key: str) -> dict:
        client = JellyfinClient(url, api_key)
        libs = [
            {"id": l.get("ItemId"), "name": l.get("Name"),
             "type": l.get("CollectionType")}
            for l in client.libraries()
        ]
        task = client.find_task(_PLUGIN_TASK_HINT)
        return {
            "libraries": libs,
            "task": {"id": task.get("Id"), "name": task.get("Name")} if task else None,
            "tasks": [{"id": t.get("Id"), "name": t.get("Name")} for t in client.tasks()],
        }

    # --- the orchestrated run --------------------------------------------
    def run(self) -> dict:
        cfg = self.get_config()
        stages: list[dict] = []

        def stage(name: str, fn) -> bool:
            try:
                msg = fn()
                stages.append({"name": name, "status": "ok", "message": msg or "done"})
                return True
            except (DispatcharrError, JellyfinError) as e:
                stages.append({"name": name, "status": "error", "message": str(e)})
                return False
            except Exception as e:  # noqa: BLE001 - surface anything to the UI
                stages.append({"name": name, "status": "error", "message": str(e)})
                return False

        d = cfg["dispatcharr"]
        j = cfg["jellyfin"]

        # 1) Dispatcharr: refresh EACH curated XC account (one per Curatarr
        #    subscription, for load-balancing), waiting for each in turn.
        acct_ids = d.get("account_ids") or []
        acct_names = d.get("account_names") or []
        if not (d.get("url") and d.get("username") and d.get("password") and acct_ids):
            stages.append({"name": "Dispatcharr refresh", "status": "skipped",
                           "message": "Dispatcharr not configured"})
        else:
            client = DispatcharrClient(d["url"], d["username"], d["password"])
            label_by_id = dict(zip(acct_ids, acct_names))
            for i, aid in enumerate(acct_ids):
                name = label_by_id.get(aid) or f"account {aid}"
                ok = stage(f"Dispatcharr: {name}",
                           lambda a=aid: self._dispatcharr_refresh(client, a))
                if not ok:
                    return {"ok": False, "stages": stages}

        # 2) Jellyfin: run the plugin sync task, then refresh chosen libraries.
        if not (j.get("url") and j.get("api_key")):
            stages.append({"name": "Jellyfin sync", "status": "skipped",
                           "message": "Jellyfin not configured"})
            return {"ok": all(s["status"] != "error" for s in stages), "stages": stages}

        client = JellyfinClient(j["url"], j["api_key"])

        task_id = j.get("task_id")
        if task_id:
            ok = stage("Jellyfin plugin sync",
                       lambda: self._jellyfin_task(client, task_id))
            if not ok:
                return {"ok": False, "stages": stages}
        else:
            stages.append({"name": "Jellyfin plugin sync", "status": "skipped",
                           "message": "No plugin sync task selected"})

        lib_ids = j.get("library_ids") or []
        lib_names = j.get("library_names") or []
        if not lib_ids:
            stages.append({"name": "Jellyfin libraries", "status": "skipped",
                           "message": "No libraries selected"})
        else:
            name_by_id = dict(zip(lib_ids, lib_names))
            for lib_id in lib_ids:
                label = f"Jellyfin library: {name_by_id.get(lib_id, lib_id)}"
                stage(label, lambda lid=lib_id: self._jellyfin_library(client, lid))

        return {"ok": all(s["status"] != "error" for s in stages), "stages": stages}

    # --- stage implementations -------------------------------------------
    def _dispatcharr_refresh(self, client: DispatcharrClient, account_id: int) -> str:
        client.refresh_account(int(account_id))
        status = client.wait_until_done(int(account_id))
        if status == "error":
            raise DispatcharrError("Dispatcharr reported an error during refresh")
        return f"refreshed ({status or 'done'})"

    def _jellyfin_task(self, client: JellyfinClient, task_id: str) -> str:
        client.run_task(task_id)
        state = client.wait_task(task_id)
        return f"plugin sync finished ({state or 'Idle'})"

    def _jellyfin_library(self, client: JellyfinClient, lib_id: str) -> str:
        client.refresh_library(lib_id)
        return "refresh triggered"
