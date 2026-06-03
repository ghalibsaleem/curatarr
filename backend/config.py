"""Environment configuration."""
from __future__ import annotations

import os


class Settings:
    db_path: str = os.environ.get("DB_PATH", "curator.db")
    # Optional initial source Xtream URL (get.php/player_api), seeded on first run.
    source_m3u: str = os.environ.get("SOURCE_M3U", "")
    # Credentials Dispatcharr uses to consume our wrapper (xc_pass generated if unset).
    xc_user: str = os.environ.get("XC_USER", "curator")
    xc_pass: str | None = os.environ.get("XC_PASS")
    frontend_dir: str = os.path.join(os.path.dirname(__file__), "..", "frontend")


settings = Settings()
