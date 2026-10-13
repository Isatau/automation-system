"""Настройки приложения — только из переменных окружения (ENV)."""
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_name: str = "RestoCore"
    database_url: str = "sqlite:///./restocore.db"
    jwt_secret: str = "change-me-via-env"          # SECRET: задавать в ENV!
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 720
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    timezone: str = "Asia/Almaty"
    currency: str = "KZT"
    backup_dir: str = "./backups"
    upload_dir: str = "./uploads"
    backup_interval_hours: int = 24
    telegram_bot_token: str = ""                    # SECRET: только ENV, никогда в БД
    fiscal_adapter: str = "stub"                    # stub | shtrih_kz | real vendor adapter
    terminal_adapter: str = "stub"
    seed_demo: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
