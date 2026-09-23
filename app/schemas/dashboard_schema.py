"""Response contract for the Dashboard API (requirement §9)."""

from datetime import date, datetime

from pydantic import BaseModel


class RecentVisitItem(BaseModel):
    id: str
    visit_at: datetime
    outcome: str | None
    lead_name: str
    project_name: str


class LockedToYouItem(BaseModel):
    lead_lock_id: str
    lead_id: str
    lead_name: str
    expires_at: datetime
    property_id: str | None
    plot_no: str | None
    project_name: str | None


class NextDayOffSummary(BaseModel):
    day_off_date: date | None
    status: str | None


class TodayAttendanceSummary(BaseModel):
    check_in_at: datetime | None
    check_out_at: datetime | None
    status: str | None


class DashboardResponse(BaseModel):
    employee_name: str
    visits_today_count: int
    active_locks_count: int
    conversions_this_week_count: int
    next_day_off: NextDayOffSummary
    today_attendance: TodayAttendanceSummary
    locked_to_you: list[LockedToYouItem]
    recent_visits: list[RecentVisitItem]
