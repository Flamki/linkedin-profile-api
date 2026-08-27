from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "LinkedIn Profile API"
    environment: str = "development"
    linkedin_li_at: SecretStr | None = None
    linkedin_jsessionid: SecretStr | None = None
    api_key: SecretStr | None = None
    headless: bool = True
    port: int = Field(default=8000, ge=1, le=65535)
    request_timeout_seconds: int = Field(default=60, ge=10, le=180)
    max_concurrent_scrapes: int = Field(default=2, ge=1, le=10)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
