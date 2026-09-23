"""Business logic for Check In/Out and attendance history (requirement §6).

Server timestamp is authoritative — the client never supplies check-in/out time.
"""

from datetime import time
from decimal import Decimal

import asyncpg

from app.core.datetime_utils import BusinessClock
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.persistence.attendance_persistence import AttendancePersistence
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.attendance_schema import AttendanceRecordResponse

_LATE_CUTOFF = time(hour=9, minute=30)
_HALF_DAY_MIN_HOURS = 4


class AttendanceService:
    def __init__(
        self,
        attendance_persistence: AttendancePersistence,
        day_off_persistence: DayOffPersistence,
        site_visit_persistence: SiteVisitPersistence,
        business_clock: BusinessClock,
    ) -> None:
        self._attendance_persistence = attendance_persistence
        self._day_off_persistence = day_off_persistence
        self._site_visit_persistence = site_visit_persistence
        self._business_clock = business_clock

    async def check_in(self, employee_id: str, latitude: Decimal | None, longitude: Decimal | None) -> AttendanceRecordResponse:
        today = self._business_clock.today()
        now = self._business_clock.now()

        is_frozen_day_off = await self._day_off_persistence.is_frozen_on_date(employee_id, today)
        if is_frozen_day_off:
            raise ConflictError("DAY_OFF_CONFLICT", "Cannot check in on a frozen Day Off")

        existing = await self._attendance_persistence.get_by_date(employee_id, today)
        if existing is not None and existing["check_in_at"] is not None:
            raise ConflictError("ALREADY_CHECKED_IN", "Already checked in today")

        status = "PRESENT" if now.time() <= _LATE_CUTOFF else "LATE"
        try:
            row = await self._attendance_persistence.create_check_in(
                employee_id, today, now, status, latitude, longitude
            )
        except asyncpg.UniqueViolationError as exc:
            # Concurrent double-tap/retry raced past the `existing is None` check above;
            # the UNIQUE(employee_id, work_date) constraint is the real guard.
            raise ConflictError("ALREADY_CHECKED_IN", "Already checked in today") from exc
        return self._to_response(row)

    async def check_out(self, employee_id: str) -> AttendanceRecordResponse:
        today = self._business_clock.today()
        now = self._business_clock.now()

        existing = await self._attendance_persistence.get_by_date(employee_id, today)
        if existing is None or existing["check_in_at"] is None:
            raise ValidationFailedError("Must check in before checking out")
        if existing["check_out_at"] is not None:
            raise ConflictError("ALREADY_CHECKED_OUT", "Already checked out today")

        worked_hours = (now - existing["check_in_at"]).total_seconds() / 3600
        has_site_visit_today = await self._site_visit_persistence.exists_on_date(employee_id, today)

        if has_site_visit_today:
            status = "ON_SITE_VISIT"
        elif worked_hours < _HALF_DAY_MIN_HOURS:
            status = "HALF_DAY"
        else:
            status = existing["status"]

        row = await self._attendance_persistence.update_check_out(employee_id, today, now, status)
        return self._to_response(row)

    async def get_today(self, employee_id: str) -> AttendanceRecordResponse | None:
        today = self._business_clock.today()
        row = await self._attendance_persistence.get_by_date(employee_id, today)
        return self._to_response(row) if row else None

    async def list_history(self, employee_id: str, page: int, page_size: int) -> tuple[list[AttendanceRecordResponse], int]:
        rows, total = await self._attendance_persistence.list_history(employee_id, page, page_size)
        return [self._to_response(row) for row in rows], total

    async def get_monthly_calendar(self, employee_id: str, year: int, month: int) -> list[AttendanceRecordResponse]:
        if month < 1 or month > 12:
            raise ValidationFailedError("Month must be between 1 and 12")
        rows = await self._attendance_persistence.list_for_month(employee_id, year, month)
        return [self._to_response(row) for row in rows]

    def _to_response(self, row: dict) -> AttendanceRecordResponse:
        return AttendanceRecordResponse(
            id=str(row["id"]),
            work_date=row["work_date"],
            check_in_at=row["check_in_at"],
            check_out_at=row["check_out_at"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
