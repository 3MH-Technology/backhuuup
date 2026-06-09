from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "Wolf Host — استضافة الذب هوست"
    developer: str = "الذئب الأبيض 🐺"
    developer_telegram: str = "@j49_c"
    channel_telegram: str = "@O5O6J"
    support_telegram: str = "@Wolfhost_1"
    x_account: str = "https://x.com/wolfhost_1"

    database_url: str = "postgresql+asyncpg://user:pass@host:5432/db"

    secret_key: str = "iM1z03uReapSZvrXJQcNtnm8BTOCsKYIk9UxwFDoEHAL45ybhV2djWq67GfglP"
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

    class Config:
        env_file = ".env"


settings = Settings()

BASE_DIR = Path(__file__).resolve().parent
