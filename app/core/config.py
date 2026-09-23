"""Centralized application settings, loaded once from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings. Never read `os.environ` directly outside this file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str
    database_pool_min_size: int = 2
    database_pool_max_size: int = 10

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    business_timezone: str = "Asia/Kolkata"

    lead_property_lock_duration_days: int = 3
    weekly_day_off_allowance: int = 1

    # Browser origins allowed to call the API (CORS), comma-separated. An Origin
    # header never has a path or trailing slash; any given here are stripped.
    cors_allowed_origins: str = (
        "https://employee.divinevisioninfra.com,"
        "http://employee.divinevisioninfra.com,"
        "https://www.employee.divinevisioninfra.com,"
        "http://www.employee.divinevisioninfra.com"
    )

    @property
    def cors_allowed_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    rate_limit_max_requests: int = 15
    rate_limit_window_seconds: int = 60

    # OP-D04 assumed default: conflict resolution is an authorized back-office
    # process outside the Employee Portal's JWT auth (no Manager/Admin role exists).
    # Required, with no default: a missing key must fail startup rather than fall
    # back to a guessable value.
    back_office_api_key: str = Field(min_length=32)

    # Lock expiry sweep. On serverless hosts (Vercel) turn the in-process timer
    # off and have an external scheduler (cron-job.org) call
    # POST /api/v1/internal/sweep with header `X-Cron-Secret: <CRON_SECRET>`.
    # The endpoint is disabled (403) while CRON_SECRET is unset.
    enable_background_lock_sweeper: bool = True
    cron_secret: str | None = Field(default=None, min_length=32)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — safe to call repeatedly as a FastAPI dependency."""
    return Settings()
