from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
from dotenv import load_dotenv
load_dotenv()

@dataclass(slots=True)
class Settings:
    """Application runtime configuration."""

    app_name: str = "Options Terminal API"
    api_url_template: str = "https://api.marketdata.app/v1/options/chain/{ticker}/"
    db_path: str = field(default_factory=lambda: os.getenv("OPTIONS_DB_PATH", "options_terminal.db"))
    marketdata_token: str = field(default_factory=lambda: os.getenv("MARKETDATA_APP_TOKEN", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    watchlist: list[str] = field(
        default_factory=lambda: [s.strip().upper() for s in os.getenv("WATCHLIST", "SPY,QQQ,AAPL").split(",") if s.strip()]
    )
    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "600")))
    dte_max: int = field(default_factory=lambda: int(os.getenv("DTE_MAX", "60")))
    cache_only: bool = field(default_factory=lambda: _env_bool("AION_CACHE_ONLY", True))
    allow_route_refresh: bool = field(default_factory=lambda: _env_bool("AION_ALLOW_ROUTE_REFRESH", False))
    marketdata_enabled: bool = field(default_factory=lambda: _env_bool("AION_MARKETDATA_ENABLED", True))


settings = Settings()
