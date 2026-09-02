"""Runtime configuration for Sentinel.

Every value can be overridden with environment variables prefixed with
``SENTINEL_`` (e.g. ``SENTINEL_DATABASE_URL``). Tuning that describes *what*
to observe — observer intervals, correlation rules, device definitions,
vendors — lives in the YAML files referenced below. Nothing is hardcoded.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings; observational detail comes from YAML."""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "sentinel"
    environment: str = "development"
    version: str = "1.0.0"
    log_level: str = "INFO"

    # --- Paths -------------------------------------------------------------
    config_dir: str = "app/config"
    data_dir: str = "./data"
    max_pagination_limit: int = 200

    # --- API ---------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8011

    # --- Storage -----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/sentinel.db"

    # --- Observers ---------------------------------------------------------
    # Comma-separated names of observers to run (global on/off switch).
    # Set to an empty string to disable every observer.
    observers: str = "system,docker,router,watcher,ups,http,network"
    observer_timeout: float = 10.0
    observer_jitter: float = 1.0

    # --- Correlation engine ------------------------------------------------
    correlation_scan_interval: float = 5.0
    correlation_window_size: int = 10_000
    correlation_recent_days: int = 1
    correlation_grace_seconds: float = 60.0

    # --- Presence engine ---------------------------------------------------
    presence_offline_after: float = 180.0
    presence_scan_interval: float = 30.0
    presence_recent_window: float = 300.0
    presence_empty_confidence: float = 0.7

    # --- Hermes integration ------------------------------------------------
    hermes_enabled: bool = True
    hermes_base_url: str = "http://127.0.0.1:8002"
    hermes_timeout: float = 5.0
    hermes_retry_attempts: int = 2
    hermes_retry_backoff: float = 1.0

    @property
    def rules_file(self) -> str:
        return f"{self.config_dir}/rules.yaml"

    @property
    def devices_file(self) -> str:
        return f"{self.config_dir}/devices.yaml"

    @property
    def vendors_file(self) -> str:
        return f"{self.config_dir}/vendors.yaml"

    @property
    def observers_file(self) -> str:
        return f"{self.config_dir}/observers.yaml"

    @property
    def hermes_event_url(self) -> str:
        return f"{self.hermes_base_url.rstrip('/')}/event"

    @property
    def enabled_observer_names(self) -> list[str]:
        return [name.strip() for name in self.observers.split(",") if name.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
