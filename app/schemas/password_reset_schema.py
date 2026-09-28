"""Request/response contracts for forgot password (email OTP → reset token → new password)."""

from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ForgotPasswordResponse(BaseModel):
    email: str


class VerifyResetOtpRequest(ForgotPasswordRequest):
    # Loosely typed on purpose: any non-matching value (wrong length, letters,
    # spaces) is reported as INVALID_OTP, not a generic validation error.
    otp: str = Field(min_length=1, max_length=20)


class VerifyResetOtpResponse(BaseModel):
    reset_token: str


class ResetPasswordRequest(BaseModel):
    reset_token: str = Field(min_length=1, max_length=200)
    # Length and content rules are checked by the service, so the error names
    # the field as `new_password` with a message written for the user.
    new_password: str = Field(max_length=1000)


class ResetPasswordResponse(BaseModel):
    ok: bool
