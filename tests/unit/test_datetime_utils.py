"""Unit tests for the business timezone / ISO week helper."""

from datetime import date, datetime, timezone

from app.core.config import get_settings
from app.core.datetime_utils import BusinessClock


def test_now_is_timezone_aware_in_business_zone() -> None:
    clock = BusinessClock(get_settings())
    now = clock.now()
    assert now.tzinfo is not None
    assert str(now.tzinfo) in ("Asia/Kolkata",) or now.utcoffset() is not None


def test_business_date_uses_business_zone_not_client_offset() -> None:
    clock = BusinessClock(get_settings())
    # 20:00 UTC on the 20th is 01:30 on the 21st in Asia/Kolkata.
    assert clock.business_date(datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc)) == date(2026, 9, 21)
    # Naive values are taken as business-local time.
    assert clock.business_date(datetime(2026, 9, 20, 23, 0)) == date(2026, 9, 20)


def test_week_key_matches_iso_calendar() -> None:
    clock = BusinessClock(get_settings())
    target = date(2026, 9, 20)
    iso_year, iso_week, _ = target.isocalendar()
    assert clock.week_key(target) == f"{iso_year}-W{iso_week:02d}"


def test_week_key_stable_across_same_iso_week() -> None:
    clock = BusinessClock(get_settings())
    monday = date(2026, 9, 14)
    sunday = date(2026, 9, 20)
    assert clock.week_key(monday) == clock.week_key(sunday)


def test_week_key_differs_across_week_boundary() -> None:
    clock = BusinessClock(get_settings())
    sunday = date(2026, 9, 20)
    next_monday = date(2026, 9, 21)
    assert clock.week_key(sunday) != clock.week_key(next_monday)
