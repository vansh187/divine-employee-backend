"""Site visit opportunity responses and permissions without database writes."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.exceptions import ForbiddenError
from app.service.site_visit_service import SiteVisitService
from tests.unit.test_opportunity_status_update import make_row, OWNER, OUTSIDER

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def service():
    return SiteVisitService(
        db=Mock(), site_visit_persistence=AsyncMock(), lead_persistence=Mock(),
        lock_persistence=Mock(), property_persistence=Mock(), day_off_persistence=Mock(),
        notification_service=Mock(), opportunity_service=Mock(), phone_normalizer=Mock(),
        business_clock=SimpleNamespace(now=lambda: NOW), settings=Mock(),
    )


def visit(opportunity):
    return {
        "id": "visit-1", "employee_id": OWNER, "lead_id": "lead-1",
        "project_id": "project-1", "property_id": None, "visit_at": NOW,
        "created_at": NOW, "updated_at": NOW, "opportunity": opportunity,
    }


@pytest.mark.parametrize("status,expired,allowed", [
    ("NEW", False, True), ("ACTIVE", False, True), ("INTERESTED", False, True),
    ("DEAL_IN_PROGRESS", False, True), ("NEW", True, False),
    ("EXPIRED", True, False), ("CONVERTED", False, False),
    ("RELEASED", False, False), ("ATTRIBUTION_CONFLICT", False, False),
])
async def test_visit_keeps_opportunity_visible_and_only_allows_valid_actions(status, expired, allowed):
    svc = service()
    opportunity = make_row(status=status, expires_at=NOW + timedelta(days=-1 if expired else 1))
    svc._site_visit_persistence.get_by_id.return_value = visit(opportunity)
    result = await svc.get_site_visit(OWNER, "visit-1")
    assert result.opportunity.id == opportunity["id"]
    assert result.opportunity.status == status
    assert result.can_update_opportunity is allowed


async def test_list_preserves_each_visits_original_opportunity_and_pagination():
    svc = service()
    old = make_row(status="EXPIRED", expires_at=NOW - timedelta(days=1))
    new = make_row(id="new-opportunity", expires_at=NOW + timedelta(days=1))
    svc._site_visit_persistence.list_for_employee.return_value = (
        [visit(old), {**visit(new), "id": "visit-2"}], 2,
    )
    items, total = await svc.list_for_employee(OWNER, 1, 20)
    assert total == 2
    assert [v.opportunity.id for v in items] == [old["id"], new["id"]]
    assert [v.can_update_opportunity for v in items] == [False, True]


async def test_missing_link_does_not_crash_or_offer_actions():
    svc = service()
    svc._site_visit_persistence.get_by_id.return_value = visit(None)
    result = await svc.get_site_visit(OWNER, "visit-1")
    assert result.opportunity is None
    assert result.can_update_opportunity is False


async def test_other_employee_cannot_read_visit_or_its_opportunity():
    svc = service()
    svc._site_visit_persistence.get_by_id.return_value = visit(make_row())
    with pytest.raises(ForbiddenError):
        await svc.get_site_visit(OUTSIDER, "visit-1")
