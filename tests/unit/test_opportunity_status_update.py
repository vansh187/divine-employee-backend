"""Unit tests for OpportunityService.update_status using in-memory fakes (no database)."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from types import SimpleNamespace

import pytest

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.service.opportunity_service import OpportunityService

OWNER = "11111111-1111-1111-1111-111111111111"
HANDLER = "22222222-2222-2222-2222-222222222222"
OUTSIDER = "33333333-3333-3333-3333-333333333333"
OPP_ID = "44444444-4444-4444-4444-444444444444"
NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def make_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": OPP_ID,
        "lead_id": "55555555-5555-5555-5555-555555555555",
        "project_id": "66666666-6666-6666-6666-666666666666",
        "property_id": None,
        "source_owner_type": "EMPLOYEE",
        "source_owner_employee_id": OWNER,
        "source_owner_channel_partner_id": None,
        "handling_employee_id": HANDLER,
        "source": "EMPLOYEE_SITE_VISIT",
        "status": "NEW",
        "attribution_status": "VERIFIED",
        "locked_at": NOW,
        "expires_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


class FakeDb:
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[object]:
        yield object()


class FakePersistence:
    def __init__(self, row: dict[str, Any] | None, update_succeeds: bool = True) -> None:
        self.row = row
        self.update_succeeds = update_succeeds
        self.set_calls: list[str] = []
        self.renewed: list[Any] = []

    async def renew(self, opportunity_id: str, expires_at: Any, connection: Any) -> None:
        self.renewed.append(expires_at)

    async def get_by_id(self, opportunity_id: str) -> dict[str, Any] | None:
        return self.row

    async def set_status_if_active(self, opportunity_id: str, new_status: str, connection: Any) -> bool:
        self.set_calls.append(new_status)
        if self.update_succeeds and self.row is not None:
            self.row = {**self.row, "status": new_status}
        return self.update_succeeds


class FakeNotifications:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.emitted: list[dict[str, Any]] = []

    async def emit(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("notification store down")
        self.emitted.append(kwargs)


class FakeDealService:
    def __init__(self, persistence: FakePersistence) -> None:
        self.persistence = persistence
        self.calls: list[tuple[str, str]] = []

    async def create_deal(self, employee_id: str, opportunity_id: str) -> None:
        self.calls.append((employee_id, opportunity_id))
        if self.persistence.row is not None:
            self.persistence.row = {**self.persistence.row, "status": "CONVERTED"}


class FakeLocks:
    def __init__(self) -> None:
        self.released: list[tuple[str, str]] = []
        self.extended: list[tuple[str, Any]] = []

    async def extend_locks_for_lead(
        self, lead_id: str, property_id: str | None, new_expires_at: Any, connection: Any
    ) -> None:
        self.extended.append((lead_id, property_id, new_expires_at))

    async def release_property_lock_for_lead(self, lead_id: str, property_id: str, connection: Any) -> None:
        self.released.append((lead_id, property_id))


class FakeLeads:
    def __init__(self) -> None:
        self.converted: list[str] = []

    async def mark_converted(self, lead_id: str) -> None:
        self.converted.append(lead_id)


PLOT_ID = "77777777-7777-7777-7777-777777777777"


def build_service(
    row: dict[str, Any] | None, update_succeeds: bool = True, notify_fails: bool = False
) -> tuple[OpportunityService, FakePersistence, FakeNotifications, FakeDealService]:
    persistence = FakePersistence(row, update_succeeds)
    notifications = FakeNotifications(fail=notify_fails)
    service = OpportunityService(FakeDb(), persistence, notifications, settings=SimpleNamespace(deal_in_progress_duration_days=15))  # type: ignore[arg-type]
    deal_service = FakeDealService(persistence)
    service._deal_service = deal_service  # type: ignore[assignment]
    service._lock_persistence = FakeLocks()  # type: ignore[assignment]
    service._lead_persistence = FakeLeads()  # type: ignore[assignment]
    return service, persistence, notifications, deal_service


async def test_lost_updates_status_and_notifies_owner_and_handler() -> None:
    service, persistence, notifications, _ = build_service(make_row())
    result = await service.update_status(OWNER, OPP_ID, "LOST")
    assert result.status == "LOST"
    assert persistence.set_calls == ["LOST"]
    assert {n["employee_id"] for n in notifications.emitted} == {OWNER, HANDLER}
    assert all(n["entity_type"] == "OPPORTUNITY" and n["entity_id"] == OPP_ID for n in notifications.emitted)


async def test_converted_with_plot_runs_deal_conversion() -> None:
    service, persistence, notifications, deal_service = build_service(make_row(property_id="77777777-7777-7777-7777-777777777777"))
    result = await service.update_status(HANDLER, OPP_ID, "CONVERTED")
    assert result.status == "CONVERTED"
    assert deal_service.calls == [(HANDLER, OPP_ID)]
    assert persistence.set_calls == []
    assert {n["event_type"] for n in notifications.emitted} == {"DEAL_LOCKED"}


async def test_converted_without_plot_only_flips_status() -> None:
    service, persistence, notifications, deal_service = build_service(make_row(property_id=None))
    result = await service.update_status(OWNER, OPP_ID, "CONVERTED")
    assert result.status == "CONVERTED"
    assert deal_service.calls == []
    assert persistence.set_calls == ["CONVERTED"]
    assert {n["event_type"] for n in notifications.emitted} == {"OPPORTUNITY_RESOLVED"}


@pytest.mark.parametrize("status", ["LOST", "RELEASED", "DEAL_REJECTED"])
async def test_lost_or_released_with_plot_frees_the_plot_lock(status: str) -> None:
    service, _, _, _ = build_service(make_row(property_id=PLOT_ID))
    await service.update_status(OWNER, OPP_ID, status)
    assert service._lock_persistence.released == [(make_row()["lead_id"], PLOT_ID)]  # type: ignore[attr-defined]


async def test_lost_without_plot_releases_nothing() -> None:
    service, _, _, _ = build_service(make_row(property_id=None))
    await service.update_status(OWNER, OPP_ID, "LOST")
    assert service._lock_persistence.released == []  # type: ignore[attr-defined]


async def test_converted_never_releases_the_plot() -> None:
    service, _, _, _ = build_service(make_row(property_id=PLOT_ID))
    await service.update_status(OWNER, OPP_ID, "CONVERTED")
    assert service._lock_persistence.released == []  # type: ignore[attr-defined]


async def test_lost_race_does_not_release_the_plot() -> None:
    service, _, _, _ = build_service(make_row(property_id=PLOT_ID), update_succeeds=False)
    with pytest.raises(ConflictError):
        await service.update_status(OWNER, OPP_ID, "LOST")
    assert service._lock_persistence.released == []  # type: ignore[attr-defined]


async def test_single_employee_is_notified_once() -> None:
    service, _, notifications, _ = build_service(make_row(handling_employee_id=None))
    await service.update_status(OWNER, OPP_ID, "RELEASED")
    assert [n["employee_id"] for n in notifications.emitted] == [OWNER]


async def test_unknown_opportunity_is_not_found() -> None:
    service, _, _, _ = build_service(None)
    with pytest.raises(NotFoundError):
        await service.update_status(OWNER, OPP_ID, "LOST")


async def test_outsider_is_forbidden() -> None:
    service, persistence, _, _ = build_service(make_row())
    with pytest.raises(ForbiddenError):
        await service.update_status(OUTSIDER, OPP_ID, "LOST")
    assert persistence.set_calls == []


@pytest.mark.parametrize("status", ["CONVERTED", "LOST", "EXPIRED", "ATTRIBUTION_CONFLICT", "RELEASED"])
async def test_non_active_opportunity_is_rejected(status: str) -> None:
    service, persistence, notifications, _ = build_service(make_row(status=status))
    with pytest.raises(ConflictError) as exc_info:
        await service.update_status(OWNER, OPP_ID, "LOST")
    assert exc_info.value.code == "OPPORTUNITY_NOT_ACTIVE"
    assert persistence.set_calls == []
    assert notifications.emitted == []


async def test_lost_race_when_status_changed_underneath() -> None:
    service, _, notifications, _ = build_service(make_row(), update_succeeds=False)
    with pytest.raises(ConflictError):
        await service.update_status(OWNER, OPP_ID, "LOST")
    assert notifications.emitted == []


async def test_notification_failure_does_not_fail_the_request() -> None:
    service, _, _, _ = build_service(make_row(), notify_fails=True)
    result = await service.update_status(OWNER, OPP_ID, "LOST")
    assert result.status == "LOST"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Deal In Progress", "DEAL_IN_PROGRESS"),
        ("Interested", "INTERESTED"),
        ("Deal Complete", "CONVERTED"),
        ("Deal Rejected", "DEAL_REJECTED"),
        ("Lost", "LOST"),
        ("Release Lock", "RELEASED"),
        ("  release-lock ", "RELEASED"),
        ("CONVERTED", "CONVERTED"),
        ("released", "RELEASED"),
    ],
)
def test_dropdown_labels_normalise(label: str, expected: str) -> None:
    from app.schemas.opportunity_schema import UpdateOpportunityStatusRequest

    assert UpdateOpportunityStatusRequest(status=label).status == expected


@pytest.mark.parametrize("bad", ["", "EXPIRED", "NEW", "new", "ACTIVE", "Deal Maybe", None, 5])
def test_unknown_labels_are_rejected(bad: object) -> None:
    from pydantic import ValidationError

    from app.schemas.opportunity_schema import UpdateOpportunityStatusRequest

    with pytest.raises(ValidationError):
        UpdateOpportunityStatusRequest(status=bad)  # type: ignore[arg-type]


async def test_interested_moves_new_to_interested_then_is_idempotent() -> None:
    service, persistence, notifications, _ = build_service(make_row())
    result = await service.update_status(OWNER, OPP_ID, "INTERESTED")
    assert result.status == "INTERESTED"
    assert persistence.set_calls == ["INTERESTED"]
    again = await service.update_status(OWNER, OPP_ID, "INTERESTED")
    assert again.status == "INTERESTED"
    assert persistence.set_calls == ["INTERESTED"]


async def test_interested_opportunity_can_then_be_lost() -> None:
    service, _, _, _ = build_service(make_row(status="INTERESTED"))
    assert (await service.update_status(OWNER, OPP_ID, "LOST")).status == "LOST"


@pytest.mark.parametrize("plot", [PLOT_ID, None])
async def test_converted_marks_lead_as_customer(plot: str | None) -> None:
    service, _, _, _ = build_service(make_row(property_id=plot, status="INTERESTED"))
    await service.update_status(OWNER, OPP_ID, "CONVERTED")
    assert service._lead_persistence.converted == [make_row()["lead_id"]]  # type: ignore[attr-defined]


async def test_lost_does_not_mark_lead_converted() -> None:
    service, _, _, _ = build_service(make_row())
    await service.update_status(OWNER, OPP_ID, "LOST")
    assert service._lead_persistence.converted == []  # type: ignore[attr-defined]


async def test_pipeline_moves_forward_and_marks_lead_customer() -> None:
    service, persistence, _, _ = build_service(make_row())
    assert (await service.update_status(OWNER, OPP_ID, "INTERESTED")).status == "INTERESTED"
    assert service._lead_persistence.converted == [make_row()["lead_id"]]  # type: ignore[attr-defined]
    assert (await service.update_status(OWNER, OPP_ID, "DEAL_IN_PROGRESS")).status == "DEAL_IN_PROGRESS"
    assert (await service.update_status(OWNER, OPP_ID, "DEAL_REJECTED")).status == "DEAL_REJECTED"
    assert persistence.set_calls == ["INTERESTED", "DEAL_IN_PROGRESS", "DEAL_REJECTED"]


async def test_cannot_move_backwards() -> None:
    service, persistence, _, _ = build_service(make_row(status="DEAL_IN_PROGRESS"))
    with pytest.raises(ConflictError) as exc_info:
        await service.update_status(OWNER, OPP_ID, "INTERESTED")
    assert exc_info.value.code == "INVALID_STATUS_TRANSITION"
    assert persistence.set_calls == []


@pytest.mark.parametrize("status", ["LOST", "RELEASED"])
async def test_lost_and_release_are_independent_exits(status: str) -> None:
    for current in ("NEW", "INTERESTED", "DEAL_IN_PROGRESS"):
        service, _, _, _ = build_service(make_row(status=current))
        assert (await service.update_status(OWNER, OPP_ID, status)).status == status


async def test_rejected_or_lost_never_marks_lead_customer() -> None:
    service, _, _, _ = build_service(make_row())
    await service.update_status(OWNER, OPP_ID, "DEAL_REJECTED")
    assert service._lead_persistence.converted == []  # type: ignore[attr-defined]


async def test_deal_in_progress_extends_opportunity_and_locks_to_15_days() -> None:
    from datetime import datetime, timedelta, timezone

    service, persistence, _, _ = build_service(make_row(status="INTERESTED", property_id=PLOT_ID))
    await service.update_status(OWNER, OPP_ID, "DEAL_IN_PROGRESS")
    assert len(persistence.renewed) == 1
    days = (persistence.renewed[0] - datetime.now(timezone.utc)) / timedelta(days=1)
    assert 14.99 < days <= 15
    assert service._lock_persistence.extended == [(make_row()["lead_id"], PLOT_ID, persistence.renewed[0])]  # type: ignore[attr-defined]


@pytest.mark.parametrize("status", ["INTERESTED", "LOST", "DEAL_REJECTED"])
async def test_other_statuses_do_not_extend_anything(status: str) -> None:
    service, persistence, _, _ = build_service(make_row(property_id=PLOT_ID))
    await service.update_status(OWNER, OPP_ID, status)
    assert persistence.renewed == []
    assert service._lock_persistence.extended == []  # type: ignore[attr-defined]
