"""Curatarr application entrypoint: create the FastAPI app, map domain errors to
HTTP responses, wire routers, and serve the frontend.

Run: uvicorn backend.main:app
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .errors import AppError
from .routers import api, xtream

app = FastAPI(title="Curatarr")


@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)


app.include_router(api.router)
app.include_router(xtream.router)


@app.get("/")
def index():
    return FileResponse(os.path.join(settings.frontend_dir, "index.html"))


# Static frontend (must be mounted last so explicit routes take precedence).
app.mount("/", StaticFiles(directory=settings.frontend_dir), name="static")
