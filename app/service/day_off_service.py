"""Business logic for the Weekly Day-Off Calendar (requirement §7-8).

Freeze policy (§13 open decision — recommended V1 default applied here):
freeze immediately on employee confirmation, no separate cutoff window.
"""

from datetime import date

from app.core.config import Settings
from app.core.datetime_utils import BusinessClock
from app.core.exceptions import DayOffAllowanceExceededError, DayOffVisitExistsError
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.day_off_schema import DayOffResponse


class DayOffService:
    def __init__(
        self,
        day_off_persistence: DayOffPersistence,
        site_visit_persistence: SiteVisitPersistence,
        business_clock: BusinessClock,
        settings: Settings,
    ) -> None:
        self._day_off_persistence = day_off_persistence
        self._site_visit_persistence = site_visit_persistence
        self._business_clock = business_clock
        self._settings = settings

    async def get_current_week(self, employee_id: str) -> DayOffResponse | None:
        week_key = self._business_clock.week_key(self._business_clock.today())
        row = await self._day_off_persistence.get_by_week(employee_id, week_key)
        return self._to_response(row) if row else None

    async def select_and_freeze(self, employee_id: str, day_off_date: date) -> DayOffResponse:
        week_key = self._business_clock.week_key(day_off_date)

        existing = await self._day_off_persistence.get_by_week(employee_id, week_key)
        if existing is not None and existing["status"] == "FROZEN" and existing["day_off_date"] != day_off_date:
            raise DayOffAllowanceExceededError(
                f"Weekly Day-Off allowance of {self._settings.weekly_day_off_allowance} already used for week {week_key}"
            )

        has_visit = await self._site_visit_persistence.exists_on_date(employee_id, day_off_date)
        if has_visit:
            raise DayOffVisitExistsError("A site visit is already scheduled/logged on this date")

        row = await self._day_off_persistence.upsert_frozen(employee_id, week_key, day_off_date)
        return self._to_response(row)

    async def is_frozen_on_date(self, employee_id: str, target_date: date) -> bool:
        return await self._day_off_persistence.is_frozen_on_date(employee_id, target_date)

    async def list_history(self, employee_id: str, page: int, page_size: int) -> tuple[list[DayOffResponse], int]:
        rows, total = await self._day_off_persistence.list_history(employee_id, page, page_size)
        return [self._to_response(row) for row in rows], total

    def _to_response(self, row: dict) -> DayOffResponse:
        display_status = row["status"]
        if display_status == "FROZEN" and row["day_off_date"] < self._business_clock.today():
            display_status = "COMPLETED"
        return DayOffResponse(
            id=str(row["id"]),
            week_key=row["week_key"],
            day_off_date=row["day_off_date"],
            status=display_status,
            selected_at=row["selected_at"],
            frozen_at=row["frozen_at"],
        )
