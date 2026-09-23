"""Aggregates the read-only Dashboard view (requirement §9) from existing persistence classes."""

import asyncio

from app.core.datetime_utils import BusinessClock
from app.core.exceptions import NotFoundError
from app.persistence.attendance_persistence import AttendancePersistence
from app.persistence.dashboard_persistence import DashboardPersistence
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.dashboard_schema import (
    DashboardResponse,
    LockedToYouItem,
    NextDayOffSummary,
    RecentVisitItem,
    TodayAttendanceSummary,
)

_RECENT_VISITS_LIMIT = 10


class DashboardService:
    def __init__(
        self,
        employee_persistence: EmployeePersistence,
        site_visit_persistence: SiteVisitPersistence,
        lock_persistence: LockPersistence,
        day_off_persistence: DayOffPersistence,
        attendance_persistence: AttendancePersistence,
        dashboard_persistence: DashboardPersistence,
        business_clock: BusinessClock,
    ) -> None:
        self._employee_persistence = employee_persistence
        self._site_visit_persistence = site_visit_persistence
        self._lock_persistence = lock_persistence
        self._day_off_persistence = day_off_persistence
        self._attendance_persistence = attendance_persistence
        self._dashboard_persistence = dashboard_persistence
        self._business_clock = business_clock

    async def get_dashboard(self, employee_id: str) -> DashboardResponse:
        employee = await self._employee_persistence.get_by_id(employee_id)
        if employee is None:
            raise NotFoundError("Employee not found")

        today = self._business_clock.today()
        week_key = self._business_clock.week_key(today)

        # None of these six reads depend on each other — each opens its own pooled
        # connection, so running them concurrently turns 6 sequential round trips
        # into 1 (the slowest of the 6), which matters since this endpoint is hit
        # on every app open.
        (
            visits_today,
            active_locks,
            conversions_this_week,
            current_week_day_off,
            today_attendance,
            (recent_visits_rows, _),
        ) = await asyncio.gather(
            self._site_visit_persistence.list_today_for_employee(employee_id, today),
            self._lock_persistence.list_active_locks_for_employee(employee_id),
            self._dashboard_persistence.count_conversions_this_week(employee_id),
            self._day_off_persistence.get_by_week(employee_id, week_key),
            self._attendance_persistence.get_by_date(employee_id, today),
            self._site_visit_persistence.list_for_employee(employee_id, page=1, page_size=_RECENT_VISITS_LIMIT),
        )

        return DashboardResponse(
            employee_name=employee["name"],
            visits_today_count=len(visits_today),
            active_locks_count=len(active_locks),
            conversions_this_week_count=conversions_this_week,
            next_day_off=NextDayOffSummary(
                day_off_date=current_week_day_off["day_off_date"] if current_week_day_off else None,
                status=current_week_day_off["status"] if current_week_day_off else None,
            ),
            today_attendance=TodayAttendanceSummary(
                check_in_at=today_attendance["check_in_at"] if today_attendance else None,
                check_out_at=today_attendance["check_out_at"] if today_attendance else None,
                status=today_attendance["status"] if today_attendance else None,
            ),
            locked_to_you=[
                LockedToYouItem(
                    lead_lock_id=str(row["lead_lock_id"]),
                    lead_id=str(row["lead_id"]),
                    lead_name=row["lead_name"],
                    expires_at=row["expires_at"],
                    property_id=str(row["property_id"]) if row["property_id"] else None,
                    plot_no=row["plot_no"],
                    project_name=row["project_name"],
                )
                for row in active_locks
            ],
            recent_visits=[
                RecentVisitItem(
                    id=str(row["id"]),
                    visit_at=row["visit_at"],
                    outcome=row.get("outcome"),
                    lead_name=row["lead_name"],
                    project_name=row["project_name"],
                )
                for row in recent_visits_rows
            ],
        )
