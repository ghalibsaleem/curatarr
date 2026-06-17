"""UI authentication endpoints (unguarded) and the require_auth dependency that
gates the internal /api/* router. Sessions ride in an httpOnly cookie."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from ..container import auth
from ..schemas import AuthCredentials, ChangePassword
from ..services.auth import COOKIE_NAME, SESSION_TTL_DAYS

router = APIRouter(prefix="/api/auth")

_MAX_AGE = SESSION_TTL_DAYS * 24 * 3600


def _set_session_cookie(response: Response, token: str) -> None:
    # httpOnly so JS can't read it; SameSite=Lax for normal navigation. Not
    # Secure — Curatarr is served over http on the LAN, where a Secure cookie
    # would never be sent.
    response.set_cookie(
        COOKIE_NAME, token, max_age=_MAX_AGE, httponly=True,
        samesite="lax", path="/",
    )


def require_auth(curatarr_session: str | None = Cookie(default=None)):
    """Dependency for the internal API: 401 unless a valid session cookie."""
    user = auth.user_for_token(curatarr_session)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


@router.get("/me")
def me(curatarr_session: str | None = Cookie(default=None)):
    user = auth.user_for_token(curatarr_session)
    return {
        "user": {"username": user["username"], "is_admin": bool(user["is_admin"])} if user else None,
        "needs_setup": auth.needs_setup(),
    }


@router.post("/setup")
def setup(req: AuthCredentials, response: Response):
    token = auth.setup(req.username, req.password)
    _set_session_cookie(response, token)
    return {"ok": True}


@router.post("/login")
def login(req: AuthCredentials, response: Response):
    token = auth.login(req.username, req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    _set_session_cookie(response, token)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response, curatarr_session: str | None = Cookie(default=None)):
    auth.logout(curatarr_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(req: ChangePassword, user=Depends(require_auth)):
    auth.change_password(user["username"], req.current_password, req.new_password)
    return {"ok": True}
