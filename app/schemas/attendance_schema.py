"""Request/response contracts for the Attendance API."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class CheckInRequest(BaseModel):
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class AttendanceRecordResponse(BaseModel):
    id: str
    work_date: date
    check_in_at: datetime | None
    check_out_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime
