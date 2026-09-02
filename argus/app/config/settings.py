"""Runtime configuration for Argus.

Every value can be overridden with environment variables prefixed with
``ARGUS_`` (e.g. ``ARGUS_DATABASE_URL``). Tuning that describes *what* to
watch — feeds, sites, providers, channels — lives in the YAML file referenced
below. Nothing is hardcoded.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings; source detail comes from YAML."""

    model_config = SettingsConfigDict(
        env_prefix="ARGUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "argus"
    environment: str = "development"
    version: str = "1.0.0"
    log_level: str = "INFO"

    # --- Paths -------------------------------------------------------------
    # Where sources.yaml lives (absolute path inside the container; ./config
    # is mounted onto /app/config so sources can be tuned without rebuilding).
    config_dir: str = "app/config"

    # --- API ---------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8012

    # --- Storage -----------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/argus.db"

    # --- Collectors --------------------------------------------------------
    # Comma-separated collector kinds to run (global on/off switch), e.g.
    # "rss,scrape,osint,telegram". Set to an empty string to disable every
    # collector.
    collectors: str = "rss,scrape,osint,telegram"
    collector_timeout: float = 30.0
    collector_jitter: float = 2.0
    # --- Collector resilience ----------------------------------------------
    # When a collector keeps failing, the scheduler backs off its next run to
    # avoid hammering a dead endpoint and spamming the log. The wait grows
    # exponentially from ``base`` up to ``max`` and resets on the first success.
    collector_backoff_base: float = 60.0
    collector_backoff_max: float = 3600.0
    # A collector is reported as *degraded* (and a Hermes notification is
    # emitted) after this many consecutive failures, and healthy again once it
    # succeeds again.
    collector_failure_threshold: int = Field(default=3, ge=1)

    # --- Intelligence layer ------------------------------------------------
    intelligence_scan_interval: float = 30.0
    extraction_enabled: bool = True
    hypothesis_enabled: bool = True
    report_min_confidence: float = 0.7

    # --- Oracle integration --------------------------------------------------
    oracle_enabled: bool = False
    oracle_base_url: str = "http://100.84.233.111:8003"
    oracle_token: str | None = None
    oracle_timeout: float = 300.0
    # How long a connection may take to open before it counts as a failure
    # (distinct from the 300s read timeout: a hung tailnet link must fail
    # fast instead of holding a worker slot).
    oracle_connect_timeout: float = 10.0
    # Transient gateway failures (network errors / 429 / 5xx) are retried with
    # backoff before the call is given up.
    oracle_retry_attempts: int = 2
    oracle_retry_backoff: float = 2.0
    oracle_extraction_enabled: bool = False
    oracle_hypothesis_enabled: bool = False
    # Only enrich items that already carry a deterministic security signal
    # (a CVE). LLM extraction is slow (tens of seconds per item) and low-signal
    # items rarely benefit, so this keeps a feed batch affordable.
    oracle_extraction_only_cves: bool = True
    # Bounded concurrency for Oracle calls: each LLM extraction takes seconds,
    # so serializing a full feed batch would stall the pipeline.
    oracle_extraction_concurrency: int = 4

    # --- Hermes integration ------------------------------------------------
    hermes_enabled: bool = True
    hermes_base_url: str = "http://127.0.0.1:8002"
    hermes_timeout: float = 5.0
    hermes_retry_attempts: int = 2
    hermes_retry_backoff: float = 1.0

    # --- Command surface (the web command center) ---------------------------
    # Bearer token required to *mutate* state (start/cancel investigations,
    # research sessions, manage dot watches). When unset, command endpoints fall
    # back to open access for LAN development. Read-only endpoints never require
    # it. The web UI stores this token and attaches it to command requests.
    command_token: str | None = None

    # --- Dot-matching investigations -----------------------------------------
    dots_enabled: bool = True
    dots_default_iterations: int = Field(default=12, ge=1, le=30)
    dots_queries_per_round: int = Field(default=3, ge=1, le=8)
    dots_max_items_per_round: int = Field(default=12, ge=1, le=50)
    dots_scrape_concurrency: int = Field(default=4, ge=1, le=16)
    dots_scrape_timeout: float = 12.0
    dots_worker_interval: float = 1.0
    # Each fresh batch is split into this many sub-batches, matched against
    # Oracle one chunk at a time, then merged back into the filtered batch.
    dots_subbatches: int = Field(default=4, ge=1, le=8)
    # Grace period Oracle gets after an entire batch has been sorted.
    dots_batch_cooldown_seconds: float = 30.0
    # Hard wall-clock budget (seconds) for one dot run before it stops.
    dots_max_run_seconds: float = 1800.0
    # Scheduled watch re-runs of dot topics.
    dots_watch_enabled: bool = True
    # How often (seconds) the watch tick sweeps for due topics.
    dots_watch_interval: float = 60.0

    # --- Research sessions ---------------------------------------------------
    # Goal-directed sessions: one question decomposed into research angles, each
    # executed as a dot investigation, closed with a synthesised report.
    research_enabled: bool = True
    # How often (seconds) the research worker tick sweeps the sessions.
    research_worker_interval: float = 5.0
    # Default number of angles a session plans (overridable per request).
    research_max_angles: int = Field(default=3, ge=1, le=6)
    # A failed angle run is retried at most this many times.
    research_max_attempts: int = Field(default=2, ge=1, le=3)
    # Adaptive loop: how many planning rounds a session may use. 1 = initial
    # plan only; each extra round re-plans from findings until Oracle says done.
    research_max_rounds: int = Field(default=3, ge=1, le=6)
    # Information-gain stopping: a new round is only started when Oracle rates
    # its expected marginal gain at least this high. 0 disables the rule.
    research_min_information_gain: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def sources_file(self) -> str:
        return f"{self.config_dir}/sources.yaml"

    @property
    def hermes_event_url(self) -> str:
        return f"{self.hermes_base_url.rstrip('/')}/event"

    @property
    def enabled_collector_names(self) -> list[str]:
        return [name.strip() for name in self.collectors.split(",") if name.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
