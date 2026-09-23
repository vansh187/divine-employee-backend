"""Request/response contracts for self-serve employee signup (email OTP)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

# bcrypt only uses the first 72 bytes of a password; a longer one would be
# silently truncated (or rejected by newer bcrypt builds), so cap it explicitly.
_BCRYPT_MAX_BYTES = 72


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    employee_id: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")
    password: str = Field(min_length=8)

    @field_validator("name", "employee_id", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password cannot be only spaces")
        if len(value.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            raise ValueError(f"Password must be at most {_BCRYPT_MAX_BYTES} bytes")
        return value


class ResendOtpRequest(BaseModel):
    email: EmailStr


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    # Loosely typed on purpose: any non-matching value (wrong length, letters,
    # spaces) is reported as INVALID_OTP, not a generic validation error.
    otp: str = Field(min_length=1, max_length=20)


class SignupPendingResponse(BaseModel):
    email: str
    expires_at: datetime  # when the emailed code stops working
