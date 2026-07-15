from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, sourced from environment variables (prefix APPROVAL_) or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APPROVAL_", extra="ignore")

    app_name: str = "approval-service"
    env: str = "local"
    # Zero-friction default for bare `make run`; docker-compose (Phase 8) points this at
    # Postgres instead, and tests override it to an in-memory SQLite DB (see conftest.py).
    database_url: str = "sqlite+aiosqlite:///./approval_service.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
