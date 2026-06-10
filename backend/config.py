import logging
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger("wolfhost.config")


class Settings(BaseSettings):
    """
    Application configuration loaded from environment variables.
    SECRET_KEY and DATABASE_URL are required — startup will fail if
    they are missing or still set to their committed placeholder values.
    """

    app_name: str = "Wolf Host — استضافة الذب هوست"
    developer: str = "الذئب الأبيض 🐺"
    developer_telegram: str = "@j49_c"
    channel_telegram: str = "@O5O6J"
    support_telegram: str = "@Wolfhost_1"
    x_account: str = "https://x.com/wolfhost_1"

    # ── Required — must be provided via environment or .env ──────────
    database_url: str  # no default: startup fails if missing
    secret_key: str    # no default: startup fails if missing

    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    max_bots_per_user: int = 3
    container_mem_limit_mb: int = 128
    container_cpu_nanos: int = 500_000_000
    container_auto_restart_max: int = 3

    log_level: str = "info"

    domain: str = "wolf-host.pages.dev"
    webhook_url: str = "https://wolf-host.pages.dev/"
    backup_git_repo: str = "https://github.com/3MH-Technology/backhuuup.git"
    backup_interval_hours: int = 6

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    @model_validator(mode="after")
    def _reject_placeholder_secrets(self) -> "Settings":
        _KNOWN_BAD_KEYS = {
            "iM1z03uReapSZvrXJQcNtnm8BTOCsKYIk9UxwFDoEHAL45ybhV2djWq67GfglP",
            "changeme",
            "",
        }
        if self.secret_key in _KNOWN_BAD_KEYS:
            raise ValueError(
                "SECRET_KEY is still set to the committed placeholder. "
                "Generate a new key (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`) "
                "and set it via environment variable."
            )
        _BAD_DB_PATTERNS = ("user:pass@host", "user:password@host", "wolfhost:changeme@localhost")
        if any(p in self.database_url for p in _BAD_DB_PATTERNS):
            raise ValueError(
                "DATABASE_URL is still set to the placeholder. "
                "Configure a real PostgreSQL connection string via environment variable."
            )
        return self

    class Config:
        env_file = ".env"
        # Allow env vars to override .env (docker-compose passes them directly)
        env_file_encoding = "utf-8"


settings = Settings()

# Warn (but don't crash) when optional services are not configured
if not settings.smtp_user or not settings.smtp_password:
    logger.warning("SMTP credentials not configured — password-reset emails will not be sent")

BASE_DIR = Path(__file__).resolve().parent
