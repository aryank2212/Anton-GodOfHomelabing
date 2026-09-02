from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden with environment
    variables prefixed with ``FORGE_`` (e.g. ``FORGE_TOKEN``).
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "forge"
    environment: str = "development"
    version: str = "1.0.0"
    log_level: str = "INFO"

    # --- Access control -----------------------------------------------------
    # Bearer token required from callers (Hermes approval bridge, Oracle agent).
    # When unset Forge refuses to run (fail-closed in production).
    token: str | None = None

    # --- Config / state / audit ---------------------------------------------
    config_file: str = "forge.yaml"
    state_file: str = "/data/forge-state.json"
    audit_file: str = "/data/forge-audit.jsonl"

    # --- Upstream services ----------------------------------------------------
    hermes_url: str = "http://hermes:8000"
    phoenix_url: str = "http://phoenix:8010"
    watcher_url: str = "http://host.docker.internal:8008"

    # --- Approvals ------------------------------------------------------------
    # Seconds an operator has to approve a Level-1 action before it expires.
    approval_timeout: float = 600.0
    approval_sweep_interval: float = 15.0

    # --- Limits -----------------------------------------------------------------
    docker_timeout: float = 20.0
    http_timeout: float = 12.0
    max_output_chars: int = 4000

    # --- Research tools ----------------------------------------------------------
    # web_search (DuckDuckGo) and fetch_url. Lower is gentler on upstreams.
    search_max_results: int = 5
    search_timeout: int = 15
    fetch_max_bytes: int = 50_000
    fetch_timeout: float = 20.0
    # Where write_note appends .txt files. Bind-mounted host path in prod.
    notes_dir: str = "/data/notes"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
