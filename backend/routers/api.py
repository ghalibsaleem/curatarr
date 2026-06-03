"""Internal /api/* endpoints consumed by the web UI."""
from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Query, Request

from ..container import catalog, imports, subscriptions, sync
from ..errors import ConfigError
from ..schemas import CountsRequest, ImportRequest, SourceRequest, UnimportRequest

router = APIRouter(prefix="/api")


@router.get("/status")
def status():
    subs = subscriptions.get_subs()
    return {
        "source_url": subs[0]["base"] if subs else "",
        "sub_count": len(subs),
        "last_sync": subscriptions.meta.get("last_sync"),
        "counts": catalog.counts(),
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


@router.post("/sync")
def do_sync():
    count = sync.run()
    return {"parsed": count, "counts": catalog.counts()}


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


@router.get("/imported")
def imported():
    return {"imported": imports.imported_list()}


@router.post("/unimport")
def unimport(req: UnimportRequest):
    return {"removed": imports.unimport(req.ids)}


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
