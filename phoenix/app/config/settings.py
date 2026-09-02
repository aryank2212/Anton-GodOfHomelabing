"""Runtime configuration for Phoenix.

All values can be overridden with environment variables prefixed with
``PHOENIX_`` (e.g. ``PHOENIX_DATABASE_URL``). The infrastructure description
— monitors, components, recovery policies, dependency graph — lives in the
YAML file referenced by ``config_file``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings; monitor/recovery details come from YAML."""

    model_config = SettingsConfigDict(
        env_prefix="PHOENIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "phoenix"
    environment: str = "development"
    version: str = "1.0.0"
    log_level: str = "INFO"

    # --- Paths -------------------------------------------------------------
    config_file: str = "app/config/phoenix.yaml"

    # --- Storage -----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/phoenix.db"
    max_pagination_limit: int = 200

    # --- API ---------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8010

    # --- Scheduler ---------------------------------------------------------
    scheduler_enabled: bool = True
    scheduler_tick_interval: float = 10.0
    scheduler_max_concurrent_checks: int = 8

    # --- Hermes integration ------------------------------------------------
    hermes_enabled: bool = True
    hermes_base_url: str = "http://127.0.0.1:8000"
    hermes_timeout: float = 5.0
    hermes_retry_attempts: int = 2
    hermes_retry_backoff: float = 1.0

    @property
    def hermes_event_url(self) -> str:
        return f"{self.hermes_base_url.rstrip('/')}/event"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
