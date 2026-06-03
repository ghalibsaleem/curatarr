"""Domain errors. Services raise these; the API layer maps them to HTTP
responses (see main.py), keeping the service/data layers framework-agnostic."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400


class ConfigError(AppError):
    """Invalid configuration or request input."""
    status_code = 400


class NotFoundError(AppError):
    status_code = 404


class ProviderError(AppError):
    """Upstream Xtream provider failed/unreachable."""
    status_code = 502
