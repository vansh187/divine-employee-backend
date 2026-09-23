"""Centralized application settings, loaded once from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
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

    # --- Employee signup (email OTP) ---
    # Only these email domains may self-register (comma-separated). Empty = any
    # domain, which lets anyone on the internet create an employee account.
    # To let test users on other domains in (e.g. gmail.com) set this in the
    # deployment's environment, not here — anyone with an address on an allowed
    # domain can create an employee account.
    signup_allowed_email_domains: str = "divinevisioninfra.com"
    # Individual addresses allowed in addition to the domains above
    # (comma-separated) — e.g. test users outside the company domain.
    signup_allowed_emails: str = ""
    signup_otp_ttl_minutes: int = Field(default=10, ge=1, le=60)
    signup_otp_max_attempts: int = Field(default=5, ge=1, le=20)
    signup_otp_resend_cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    signup_otp_max_sends_per_hour: int = Field(default=5, ge=1, le=50)
    signup_pending_ttl_hours: int = Field(default=24, ge=1, le=168)

    # --- Outgoing email ---
    # "smtp" sends real email (production). "log" writes the message to the
    # server log instead — local development only, never on a shared host.
    email_delivery_mode: Literal["smtp", "log"] = "smtp"
    # Resend: setting RESEND_API_KEY alone is enough — it fills in Resend's SMTP
    # relay (smtp.resend.com:465, SSL, username "resend"). The SMTP_* settings
    # override it, or configure any other SMTP provider instead.
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_ssl: bool = True  # True: implicit TLS (465). False: STARTTLS (587).
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=60)
    # Sender address; must be on a domain verified with the provider.
    email_from_address: str | None = Field(
        default=None, validation_alias=AliasChoices("EMAIL_FROM_ADDRESS", "DIVINE_RESEND_EMAIL")
    )
    email_from_name: str = "Divine Vision Infra"

    @property
    def effective_smtp_host(self) -> str | None:
        return self.smtp_host or ("smtp.resend.com" if self.resend_api_key else None)

    @property
    def effective_smtp_username(self) -> str | None:
        return self.smtp_username or ("resend" if self.resend_api_key else None)

    @property
    def effective_smtp_password(self) -> str | None:
        return self.smtp_password or self.resend_api_key

    @property
    def signup_allowed_email_domain_list(self) -> list[str]:
        return [
            domain.strip().lower().lstrip("@")
            for domain in self.signup_allowed_email_domains.split(",")
            if domain.strip()
        ]

    @property
    def signup_allowed_email_list(self) -> list[str]:
        return [email.strip().lower() for email in self.signup_allowed_emails.split(",") if email.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — safe to call repeatedly as a FastAPI dependency."""
    return Settings()
