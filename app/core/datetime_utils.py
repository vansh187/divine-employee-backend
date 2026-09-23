"""Timezone-aware date helpers built around the single configured business timezone."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings


class BusinessClock:
    """Instance-based helper so the business timezone is injected, never hard-coded."""

    def __init__(self, settings: Settings) -> None:
        self._zone = ZoneInfo(settings.business_timezone)

    def now(self) -> datetime:
        return datetime.now(self._zone)

    def today(self) -> date:
        return self.now().date()

    def localize(self, value: datetime) -> datetime:
        """Converts to the business timezone; a naive value is taken as business-local time."""
        if value.tzinfo is None:
            return value.replace(tzinfo=self._zone)
        return value.astimezone(self._zone)

    def business_date(self, value: datetime) -> date:
        """Calendar date of `value` in the business timezone, whatever offset the client sent."""
        return self.localize(value).date()

    def week_key(self, target_date: date) -> str:
        iso_year, iso_week, _ = target_date.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
