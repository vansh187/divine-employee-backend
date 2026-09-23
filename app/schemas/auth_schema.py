"""Request/response contracts for the Auth API."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class EmployeeProfileResponse(BaseModel):
    id: str
    employee_code: str
    name: str
    email: str
    phone: str | None
    team: str | None
    designation: str | None
    status: str
    weekly_day_off_allowance: int
    business_timezone: str
    created_at: datetime
    updated_at: datetime
