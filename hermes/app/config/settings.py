from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden with environment
    variables prefixed with ``HERMES_`` (e.g. ``HERMES_DATABASE_URL``).
    Secrets must only ever come from the environment / .env file.
    """

    model_config = SettingsConfigDict(
        env_prefix="HERMES_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- General -----------------------------------------------------------
    app_name: str = "hermes"
    environment: str = "development"
    version: str = "1.0.0"
    log_level: str = "INFO"

    # --- Storage / rules / templates ---------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/hermes.db"
    rules_file: str = "app/config/rules.yaml"
    templates_dir: str = "app/templates"
    max_pagination_limit: int = 100

    # --- Queue / delivery ---------------------------------------------------
    worker_concurrency: int = 4
    worker_sweep_interval: float = 30.0
    notification_max_attempts: int = 3
    notification_retry_base_delay: float = 1.0

    # --- Discord ------------------------------------------------------------
    discord_webhook_url: str | None = None

    # --- Telegram -----------------------------------------------------------
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # --- Email (SMTP) -------------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    # --- ntfy ---------------------------------------------------------------
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str | None = None
    ntfy_token: str | None = None

    # --- Generic webhook ----------------------------------------------------
    webhook_url: str | None = None
    webhook_secret: str | None = None

    # --- Storm detection ----------------------------------------------------
    storm_enabled: bool = False
    storm_window_seconds: float = 60.0
    storm_threshold: int = 20
    storm_cooldown_seconds: float = 300.0
    storm_check_interval: float = 10.0

    # --- Remediation (act, not just notify) ---------------------------------
    remediation_enabled: bool = False
    remediation_max_attempts: int = 1
    remediation_retry_base_delay: float = 1.0
    remediation_allowed_commands: str = ""

    # --- Telegram inbound bot -----------------------------------------------
    bot_enabled: bool = False
    bot_allow_cmd: bool = False
    bot_allowed_commands: str = ""
    bot_max_output_chars: int = 3500
    bot_rate_limit_per_minute: int = 10
    netdata_url: str = "http://127.0.0.1:19999"
    # Argus (internet intelligence) health endpoint for the /argus command.
    bot_argus_url: str = "http://127.0.0.1:8012"

    # --- AI / Oracle ---------------------------------------------------------
    # Answers non-command Telegram messages by asking the Oracle gateway
    # (on Lappy) over Tailscale. Hermes itself never loads models.
    ai_enabled: bool = False
    oracle_url: str | None = None
    oracle_token: str | None = None
    ai_max_history: int = 10
    ai_timeout: float = 60.0
    # Route plain-text replies through the gateway's tool-calling loop
    # (/v1/agent) so the AI can run Forge actions under policy + approvals.
    # Falls back to /v1/ask when the gateway does not expose the endpoint yet.
    ai_use_agent: bool = True
    # Send a snapshot of Hermes' live state (events, alerts, health) to the
    # gateway so the model can answer questions about Anton.
    ai_context_enabled: bool = True
    ai_context_events: int = 10
    # Conversation turns are kept at most this long (survives restarts).
    ai_history_max_age_days: int = 30

    # --- AI watchdog (monitor + manager) --------------------------------------
    # A background loop that watches the event stream and asks the Oracle
    # gateway whether an allow-listed recovery action is warranted. Low-risk
    # proposals auto-run after a veto window unless the operator replies.
    watchdog_enabled: bool = False
    watchdog_check_interval: float = 30.0
    # Seconds an operator has to veto a low-risk proposal before it runs.
    watchdog_confirm_seconds: float = 180.0
    # Minimum seconds between proposing an action for the same target.
    watchdog_target_cooldown_seconds: float = 900.0
    # Once a target has this many executed watchdog actions inside the window
    # below, it is treated as crash-looping and further proposals for it
    # require operator approval instead of auto-running.
    watchdog_crashloop_threshold: int = 3
    watchdog_crashloop_window_seconds: float = 3600.0
    # Most events considered per check (keeps LLM calls bounded).
    watchdog_max_events_per_check: int = 3
    # Fail-closed: proposals whose rendered command does not match these
    # fnmatch patterns are never executed. Empty list disables the watchdog.
    watchdog_allowed_commands: str = (
        "docker restart *,docker start *,docker stop *,docker logs *,"
        "docker inspect *,docker ps *"
    )

    # --- Forge approval bridge --------------------------------------------------
    # Forge is the execution layer: at Level 1 its act tools require a human
    # thumbs-up. Forge POSTs an approval request here; Hermes relays it over
    # Telegram and routes the operator's yes/no reply back to Forge's resolve
    # endpoint. Hermes never decides — it only transports.
    forge_enabled: bool = False
    forge_url: str | None = None
    forge_token: str | None = None
    forge_approval_timeout: float = 900.0
    forge_approval_sweep_interval: float = 30.0

    @property
    def smtp_recipients(self) -> list[str]:
        """Comma-separated recipient list from ``HERMES_SMTP_TO``."""
        return _split_csv(self.smtp_to)

    @property
    def remediation_command_patterns(self) -> list[str]:
        """Glob patterns restricting which ``command`` remediations may run."""
        return _split_csv(self.remediation_allowed_commands)

    @property
    def bot_command_patterns(self) -> list[str]:
        """Glob patterns restricting what the bot's ``/cmd`` may run."""
        return _split_csv(self.bot_allowed_commands)

    @property
    def watchdog_command_patterns(self) -> list[str]:
        """Glob patterns restricting which commands the AI watchdog may run."""
        return _split_csv(self.watchdog_allowed_commands)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
