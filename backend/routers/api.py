"""Internal /api/* endpoints consumed by the web UI."""
from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from .. import __version__
from .auth import require_auth
from ..container import catalog, downstream, imports, scheduler, subscriptions, sync
from ..errors import ConfigError, ProviderError
from ..providers.dispatcharr_client import DispatcharrError
from ..providers.jellyfin_client import JellyfinError
from ..schemas import (
    CountsRequest,
    DiscoverDispatcharr,
    DiscoverJellyfin,
    DownstreamConfig,
    ImportRequest,
    ScheduleConfig,
    SourceRequest,
    Sub,
    UnimportRequest,
)

# Every internal endpoint requires a logged-in session. Auth endpoints live on a
# separate unguarded router; the public Xtream surface has its own credentials.
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def _test_result(fn):
    """Run a connection test, returning {ok, message} either way so the UI can
    show inline pass/fail instead of treating a failure as a thrown error."""
    try:
        return {"ok": True, "message": fn()}
    except Exception as e:  # noqa: BLE001 - any failure is reported to the user
        return {"ok": False, "message": str(e)}


@router.get("/status")
def status():
    subs = subscriptions.get_subs()
    sched = scheduler.status()
    return {
        "version": __version__,
        "source_url": subs[0]["base"] if subs else "",
        "sub_count": len(subs),
        "last_sync": subscriptions.meta.get("last_sync"),
        "counts": catalog.counts(),
        "auto_sync": sched["config"]["enabled"],
        "next_sync": sched["next_sync"],
        "last_auto_sync": sched["last_result"],
    }


@router.get("/source")
def get_source():
    return {
        "subs": [
            {"url": s["base"], "username": s["user"], "password": s["pass"]}
            for s in subscriptions.get_subs()
        ],
        "last_sync": subscriptions.meta.get("last_sync"),
    }


@router.post("/source")
def set_source(req: SourceRequest):
    if not req.subs:
        raise ConfigError("At least one subscription is required")
    subs = []
    for s in req.subs:
        url, user, pwd = s.url.strip(), s.username.strip(), s.password.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ConfigError("Each Server URL must start with http:// or https://")
        if not (user and pwd):
            raise ConfigError("Each subscription needs a username and password")
        parts = urllib.parse.urlsplit(url)
        subs.append({"base": f"{parts.scheme}://{parts.netloc}", "user": user, "pass": pwd})
    subscriptions.set_subs(subs)
    return {"subs": [{"url": s["base"], "username": s["user"]} for s in subs]}


@router.post("/source/test")
def source_test(req: Sub):
    return _test_result(lambda: subscriptions.test_source(req.url, req.username, req.password))


@router.post("/sync")
def do_sync():
    count = sync.run()
    return {"parsed": count, "counts": catalog.counts()}


@router.get("/schedule")
def get_schedule():
    return scheduler.status()


@router.post("/schedule")
def set_schedule(req: ScheduleConfig):
    scheduler.set_config(req.model_dump())
    return scheduler.status()


@router.post("/schedule/preview")
def preview_schedule(req: ScheduleConfig):
    nxt = scheduler.preview(req.model_dump())
    return {"next_sync": nxt.isoformat(timespec="seconds") if nxt else None}


@router.get("/groups")
def groups(kind: str = Query(..., pattern="^(live|movie|series)$")):
    return {"groups": catalog.groups(kind)}


@router.get("/items")
def items(
    kind: str = Query(..., pattern="^(live|movie)$"),
    group: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=500),
):
    return catalog.list_items(kind, group, q, page, page_size)


@router.get("/series")
def series(
    group: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=500),
):
    return catalog.list_series(group, q, page, page_size)


@router.post("/series/counts")
def series_counts(req: CountsRequest):
    return {"counts": catalog.series_counts(req.series_keys)}


@router.get("/series/detail")
def series_detail(series_key: str):
    return catalog.series_detail(series_key)


@router.post("/import")
def do_import(req: ImportRequest):
    return imports.do_import(req.ids, req.series_key, req.season, req.episode_ids)


@router.post("/import-m3u")
async def import_m3u(file: UploadFile = File(...)):
    raw = await file.read()
    text = raw.decode("utf-8", "replace")
    return imports.import_m3u(text)


@router.get("/imported")
def imported():
    return {"imported": imports.imported_list()}


@router.post("/unimport")
def unimport(req: UnimportRequest):
    return {"removed": imports.unimport(req.ids)}


@router.get("/downstream/config")
def downstream_config():
    return downstream.get_config()


@router.post("/downstream/config")
def set_downstream_config(req: DownstreamConfig):
    cfg = {}
    if req.dispatcharr is not None:
        cfg["dispatcharr"] = req.dispatcharr.model_dump()
    if req.jellyfin is not None:
        cfg["jellyfin"] = req.jellyfin.model_dump()
    return downstream.set_config(cfg)


@router.post("/downstream/dispatcharr/accounts")
def downstream_dispatcharr_accounts(req: DiscoverDispatcharr):
    try:
        return {"accounts": downstream.dispatcharr_accounts(req.url, req.username, req.password)}
    except DispatcharrError as e:
        raise ProviderError(str(e))


@router.post("/downstream/dispatcharr/test")
def downstream_dispatcharr_test(req: DiscoverDispatcharr):
    return _test_result(lambda: downstream.dispatcharr_test(req.url, req.username, req.password))


@router.post("/downstream/jellyfin/discover")
def downstream_jellyfin_discover(req: DiscoverJellyfin):
    try:
        return downstream.jellyfin_discover(req.url, req.api_key)
    except JellyfinError as e:
        raise ProviderError(str(e))


@router.post("/downstream/jellyfin/test")
def downstream_jellyfin_test(req: DiscoverJellyfin):
    return _test_result(lambda: downstream.jellyfin_test(req.url, req.api_key))


@router.post("/downstream/run")
def downstream_run():
    return downstream.run()


@router.get("/xc-info")
def xc_info(request: Request):
    base = str(request.base_url).rstrip("/")
    accounts = [{
        "username": a["username"],
        "password": a["password"],
        "player_api": f"{base}/player_api.php?username={a['username']}&password={a['password']}",
    } for a in subscriptions.out_accounts()]
    return {
        "server_url": base,
        "accounts": accounts,
        "note": "Add EACH account in Dispatcharr as an Xtream Codes account "
                "(VOD scanning ON, max connections 1). Dispatcharr will balance "
                "streams across them.",
    }
