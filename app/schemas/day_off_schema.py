"""Request/response contracts for the Weekly Day-Off API."""

from datetime import date, datetime

from pydantic import BaseModel


class SelectDayOffRequest(BaseModel):
    day_off_date: date


class DayOffResponse(BaseModel):
    id: str
    week_key: str
    day_off_date: date
    status: str
    selected_at: datetime | None
    frozen_at: datetime | None
