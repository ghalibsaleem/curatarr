"""Public Xtream Codes endpoints consumed by Dispatcharr (and any XC client)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from ..container import panel, subscriptions
from ..errors import NotFoundError

router = APIRouter()


@router.get("/player_api.php")
@router.get("/panel_api.php")
def player_api(request: Request):
    p = dict(request.query_params)
    if not subscriptions.check_creds(p.get("username", ""), p.get("password", "")):
        return JSONResponse({"user_info": {"auth": 0}, "server_info": {}})
    base = str(request.base_url).rstrip("/")
    return JSONResponse(panel.dispatch(p.get("action"), p, base))


@router.get("/xmltv.php")
def xmltv():
    # We don't provide EPG; return a valid empty TV document.
    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?>\n<tv></tv>',
        media_type="application/xml",
    )


def _redirect(username: str, password: str, filename: str):
    if not subscriptions.check_creds(username, password):
        raise HTTPException(403, "Forbidden")
    sub = subscriptions.sub_for_user(username)
    stream_id = filename.rsplit(".", 1)[0]
    url = panel.redirect_url(sub, stream_id)
    if not url:
        raise NotFoundError("Unknown stream")
    # Dispatcharr's VOD proxy follows this to the provider (we stay out of the
    # byte path); credentials in the target URL are the provider's own.
    return RedirectResponse(url, status_code=302)


@router.get("/movie/{username}/{password}/{filename}")
def stream_movie(username: str, password: str, filename: str):
    return _redirect(username, password, filename)


@router.get("/series/{username}/{password}/{filename}")
def stream_series(username: str, password: str, filename: str):
    return _redirect(username, password, filename)


@router.get("/live/{username}/{password}/{filename}")
def stream_live(username: str, password: str, filename: str):
    return _redirect(username, password, filename)
