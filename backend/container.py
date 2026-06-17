"""Composition root: build the single Database, repositories, and services, and
run first-run seeding. Routers import the wired singletons from here."""
from __future__ import annotations

import secrets

from .config import settings
from .db import Database
from .providers.xtream_client import creds_from_source
from .repositories.items import ItemsRepo
from .repositories.ledger import LedgerRepo
from .repositories.meta import MetaRepo
from .services.catalog import CatalogService
from .services.downstream import DownstreamService
from .services.imports import ImportService
from .services.scheduler import SchedulerService
from .services.subscriptions import SubscriptionsService
from .services.sync import SyncService
from .services.xtream_panel import XtreamPanelService

# Infrastructure + data layer
db = Database(settings.db_path)
meta = MetaRepo(db)
items = ItemsRepo(db)
ledger = LedgerRepo(db)

# Service layer
subscriptions = SubscriptionsService(meta)
catalog = CatalogService(subscriptions, items, ledger)
sync = SyncService(subscriptions, items, ledger, meta)
imports = ImportService(items, ledger, catalog)
panel = XtreamPanelService(ledger)
downstream = DownstreamService(meta)
scheduler = SchedulerService(meta, sync, downstream)


def _seed() -> None:
    """First-run seeding: migrate a legacy source into discrete fields and
    ensure the wrapper credentials exist."""
    if meta.get("src_base") is None:
        seed = meta.get("source_url") or settings.source_m3u
        if seed:
            base, user, pwd = creds_from_source(seed)
            if base:
                meta.set("src_base", base)
            if user:
                meta.set("src_user", user)
            if pwd:
                meta.set("src_pass", pwd)
    if meta.get("xc_user") is None:
        meta.set("xc_user", settings.xc_user)
    if meta.get("xc_pass") is None:
        meta.set("xc_pass", settings.xc_pass or secrets.token_hex(8))


_seed()
