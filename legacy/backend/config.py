import os
import json
import yaml
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = os.getenv("LEGACY_CONFIG", str(BASE_DIR / "config.yaml"))


class Config:
    _instance: Optional["Config"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self):
        if self._loaded:
            return
        self._loaded = True
        self._data = self._load()

    def _load(self) -> dict:
        path = CONFIG_PATH
        defaults = {
            "app": {
                "name": "LEGACY",
                "version": "3.0.0",
                "host": "0.0.0.0",
                "port": 8000,
                "secret_key": os.getenv("SECRET_KEY", "change-me-in-production"),
                "session_ttl_hours": 24,
                "cors_origins": os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else [],
                "debug": False,
            },
            "database": {
                "url": os.getenv("DATABASE_URL", "sqlite:///database/legacy.db"),
                "pool_size": 5,
                "max_overflow": 10,
                "echo": False,
            },
            "embedding": {
                "provider": "sentence-transformers",
                "model": "all-MiniLM-L6-v2",
                "enabled": True,
                "batch_size": 16,
                "cache_size": 1000,
            },
            "llm": {
                "provider": "ollama",
                "url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
                "model": os.getenv("LLM_MODEL", "llama3.2"),
                "enabled": True,
            },
            "queue": {
                "max_workers": 4,
                "poll_interval": 1,
                "max_retries": 3,
                "retry_delay": 60,
            },
            "logging": {
                "level": os.getenv("LOG_LEVEL", "INFO"),
                "format": "json",
                "file": os.getenv("LEGACY_LOG_FILE", str(BASE_DIR / "logs" / "legacy.log")),
                "max_bytes": 10485760,
                "backup_count": 7,
                "rotation": "daily",
            },
            "backup": {
                "directory": str(BASE_DIR / "backups"),
                "schedule": "0 3 * * *",
                "max_backups": 30,
                "encrypt": False,
                "encryption_key": "",
            },
            "memory": {
                "max_content_length": 100000,
                "default_visibility": "private",
                "pagination_default": 50,
                "pagination_max": 200,
            },
            "auth": {
                "enabled": True,
                "session_ttl": 86400,
                "bcrypt_rounds": 12,
                "default_role": "user",
            },
            "email": {
                "enabled": True,
                "send_method": "console",
                "from_name": "LEGACY",
                "from_email": "noreply@redclove.space",
                "smtp_host": os.getenv("SMTP_HOST", ""),
                "smtp_port": int(os.getenv("SMTP_PORT", "587")),
                "smtp_user": os.getenv("SMTP_USER", ""),
                "smtp_password": os.getenv("SMTP_PASSWORD", ""),
                "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
                "use_ssl": os.getenv("SMTP_USE_SSL", "false").lower() == "true",
            },
            "otp": {
                "length": 6,
                "ttl_seconds": 600,
                "max_attempts": 5,
                "resend_cooldown_seconds": 60,
            },
            "monitoring": {
                "enabled": True,
                "prometheus": False,
            },
            "security": {
                "trusted_hosts": [
                    "journal.redclove.space",
                    "anton.osiris-everest.ts.net",
                    "localhost",
                ],
                "rate_limits": {
                    "login_max": 5,
                    "login_window": 900,
                    "login_ip_max": 30,
                    "login_ip_window": 900,
                    "register_max": 5,
                    "register_window": 3600,
                    "otp_send_max": 5,
                    "otp_send_window": 3600,
                    "otp_verify_max": 10,
                    "otp_verify_window": 900,
                },
                "force_password_change_on_default": True,
            },
        }
        if os.path.exists(path):
            try:
                with open(path) as f:
                    user = yaml.safe_load(f) or {}
                self._deep_merge(defaults, user)
            except Exception:
                pass
        return defaults

    def _deep_merge(self, base: dict, override: dict):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def __getattr__(self, name: str) -> dict:
        return self._data.get(name, {})


config = Config()
