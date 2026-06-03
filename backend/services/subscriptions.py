"""Provider subscriptions and the Xtream output accounts derived from them.

Each subscription gets a STABLE output username (curator, curator2, …) tied to
its credentials, so adding/removing/reordering subs never renames the others.
One output account per sub lets Dispatcharr load-balance across them.
"""
from __future__ import annotations

import json
import secrets

from ..errors import ConfigError
from ..providers.xtream_client import ProviderXC
from ..repositories.meta import MetaRepo


class SubscriptionsService:
    def __init__(self, meta: MetaRepo):
        self.meta = meta

    # --- subscriptions ----------------------------------------------------
    def _base_user(self) -> str:
        return self.meta.get("xc_user") or "curator"

    def _next_out_user(self, used: set[str]) -> str:
        bu = self._base_user()
        i = 1
        while True:
            name = bu if i == 1 else f"{bu}{i}"
            if name not in used:
                return name
            i += 1

    def get_subs(self) -> list[dict]:
        """[{base,user,pass,out_user}]. Migrates a legacy single source and older
        subs missing out_user, assigning stable names."""
        raw = self.meta.get("subs")
        if raw:
            subs = json.loads(raw)
        else:
            base, user, pwd = self.meta.get("src_base"), self.meta.get("src_user"), self.meta.get("src_pass")
            subs = [{"base": base, "user": user, "pass": pwd}] if (base and user and pwd) else []
        changed = not raw and bool(subs)
        used = {s["out_user"] for s in subs if s.get("out_user")}
        for s in subs:
            if not s.get("out_user"):
                s["out_user"] = self._next_out_user(used)
                used.add(s["out_user"])
                changed = True
        if changed:
            self.meta.set("subs", json.dumps(subs))
        return subs

    def set_subs(self, new_list: list[dict]) -> list[dict]:
        """Persist subs from {base,user,pass} input, preserving each existing
        sub's stable out_user (matched by credentials)."""
        by_key = {(s["base"], s["user"], s["pass"]): s["out_user"] for s in self.get_subs()}
        used = set(by_key.values())
        result: list[dict] = []
        for s in new_list:
            ou = by_key.get((s["base"], s["user"], s["pass"]))
            if not ou or ou in {r["out_user"] for r in result}:
                ou = self._next_out_user(used | {r["out_user"] for r in result})
            result.append({"base": s["base"], "user": s["user"], "pass": s["pass"], "out_user": ou})
        self.meta.set("subs", json.dumps(result))
        return result

    # --- output accounts --------------------------------------------------
    def out_accounts(self) -> list[dict]:
        pwd = self.meta.get("xc_pass") or ""
        return [{"username": s["out_user"], "password": pwd, "sub": s} for s in self.get_subs()]

    def sub_for_user(self, username: str) -> dict | None:
        for a in self.out_accounts():
            if a["username"] == username:
                return a["sub"]
        return None

    def check_creds(self, username: str, password: str) -> bool:
        pwd = self.meta.get("xc_pass") or ""
        users = {a["username"] for a in self.out_accounts()}
        return username in users and secrets.compare_digest(password, pwd)

    # --- provider ---------------------------------------------------------
    def primary_provider(self) -> ProviderXC:
        """Client for ingest — reads the primary subscription (subs[0]). All subs
        are the same panel, so one read covers the shared catalogue."""
        subs = self.get_subs()
        if not subs:
            raise ConfigError("Configure at least one provider subscription first")
        s = subs[0]
        return ProviderXC(s["base"], s["user"], s["pass"])
