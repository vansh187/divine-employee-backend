"""Edge cases across the status pipeline, holds and concurrency. Every response must be a clean 2xx/4xx, never a 500."""

import asyncio
import uuid
from datetime import date

from app.main import app
from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from tests.conftest import auth_headers


def _phone() -> str:
    return "9" + str(uuid.uuid4().int)[:9]


def _payload(place: dict, phone: str, with_plot: bool = True) -> dict:
    payload = {
        "visitor_name": "Edge Lead",
        "phone": phone,
        "project_id": place["project_id"],
        "visit_at": f"{date.today().isoformat()}T11:00:00+05:30",
    }
    if with_plot:
        payload["property_id"] = place["property_id"]
    return payload


async def _visit(client, headers, place, phone: str, with_plot: bool = True) -> dict:
    response = await client.post("/api/v1/site-visits", headers=headers, json=_payload(place, phone, with_plot))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _set(client, headers, opportunity_id: str, status: str):
    return await client.post(f"/api/v1/opportunities/{opportunity_id}/status", headers=headers, json={"status": status})


async def test_full_pipeline_to_deal_complete_with_real_db(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    visit = await _visit(client, headers, place, _phone())
    opportunity_id = visit["opportunity"]["id"]

    steps = (("Interested", "INTERESTED"), ("Deal In Progress", "DEAL_IN_PROGRESS"), ("Deal Complete", "CONVERTED"))
    for label, expected in steps:
        response = await _set(client, headers, opportunity_id, label)
        assert response.status_code == 200, (label, response.text)
        assert response.json()["data"]["status"] == expected

    assert await raw_conn.fetchval(
        "SELECT lifecycle_status FROM leads WHERE id = $1", uuid.UUID(visit["lead_id"])
    ) == "CONVERTED"
    assert await raw_conn.fetchval(
        "SELECT status FROM properties WHERE id = $1", uuid.UUID(place["property_id"])
    ) == "DEAL_LOCKED"
    deals = (await client.get("/api/v1/deals", headers=headers)).json()["data"]
    assert [d["opportunity_id"] for d in deals] == [opportunity_id]
    assert (await _set(client, headers, opportunity_id, "LOST")).status_code == 409


async def test_deal_in_progress_then_rejected_frees_plot_and_cannot_go_backwards(
    client, make_employee, make_project_and_plot, raw_conn
):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity_id = (await _visit(client, headers, place, _phone()))["opportunity"]["id"]

    assert (await _set(client, headers, opportunity_id, "DEAL_IN_PROGRESS")).status_code == 200
    backwards = await _set(client, headers, opportunity_id, "INTERESTED")
    assert backwards.status_code == 409
    assert backwards.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    rejected = await _set(client, headers, opportunity_id, "Deal Rejected")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "DEAL_REJECTED"
    plot = uuid.UUID(place["property_id"])
    assert await raw_conn.fetchval("SELECT status FROM properties WHERE id = $1", plot) == "AVAILABLE"
    assert await raw_conn.fetchval("SELECT status FROM property_locks WHERE property_id = $1", plot) == "RELEASED"
    assert (await _set(client, headers, opportunity_id, "LOST")).status_code == 409


async def test_deal_in_progress_survives_the_expiry_sweep_but_new_does_not(
    client, make_employee, make_project_and_plot, raw_conn
):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    in_progress = (await _visit(client, headers, await make_project_and_plot(), _phone()))["opportunity"]["id"]
    untouched = (await _visit(client, headers, await make_project_and_plot(), _phone()))["opportunity"]["id"]
    assert (await _set(client, headers, in_progress, "DEAL_IN_PROGRESS")).status_code == 200

    # Four days pass: the 3-day window lapses for `untouched`, the 15-day one for `in_progress` does not.
    await raw_conn.execute("UPDATE opportunities SET expires_at = expires_at - interval '4 days'")
    await raw_conn.execute("UPDATE lead_locks SET expires_at = expires_at - interval '4 days'")
    await raw_conn.execute("UPDATE property_locks SET expires_at = expires_at - interval '4 days'")

    db = app.state.db
    await LockPersistence(db).release_expired_locks()
    await OpportunityPersistence(db).expire_stale_opportunities()

    assert await raw_conn.fetchval(
        "SELECT status FROM opportunities WHERE id = $1", uuid.UUID(in_progress)
    ) == "DEAL_IN_PROGRESS"
    assert await raw_conn.fetchval("SELECT status FROM opportunities WHERE id = $1", uuid.UUID(untouched)) == "EXPIRED"


async def test_concurrent_visits_to_one_plot_never_500(client, make_employee, make_project_and_plot, raw_conn):
    first = await make_employee()
    second = await make_employee()
    place = await make_project_and_plot()
    first_headers = await auth_headers(client, first["email"], first["password"])
    second_headers = await auth_headers(client, second["email"], second["password"])

    responses = await asyncio.gather(
        client.post("/api/v1/site-visits", headers=first_headers, json=_payload(place, _phone())),
        client.post("/api/v1/site-visits", headers=second_headers, json=_payload(place, _phone())),
    )
    assert all(r.status_code in (200, 409) for r in responses), [r.text for r in responses]
    assert any(r.status_code == 200 for r in responses)
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM property_locks WHERE property_id = $1 AND status = 'ACTIVE'",
        uuid.UUID(place["property_id"]),
    ) == 1


async def test_concurrent_visits_for_one_lead_never_500(client, make_employee, make_project_and_plot, raw_conn):
    first = await make_employee()
    second = await make_employee()
    place = await make_project_and_plot()
    other_place = await make_project_and_plot()
    first_headers = await auth_headers(client, first["email"], first["password"])
    second_headers = await auth_headers(client, second["email"], second["password"])
    phone = _phone()

    responses = await asyncio.gather(
        client.post("/api/v1/site-visits", headers=first_headers, json=_payload(place, phone)),
        client.post("/api/v1/site-visits", headers=second_headers, json=_payload(other_place, phone)),
    )
    assert all(r.status_code in (200, 409) for r in responses), [r.text for r in responses]
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM lead_locks ll JOIN leads l ON l.id = ll.lead_id "
        "WHERE ll.status = 'ACTIVE' AND l.normalized_phone LIKE '%' || $1",
        phone,
    ) == 1


async def test_visit_after_holders_lock_lapsed_takes_plot_without_sweep(
    client, make_employee, make_project_and_plot, raw_conn
):
    holder = await make_employee()
    visitor = await make_employee()
    place = await make_project_and_plot()
    holder_headers = await auth_headers(client, holder["email"], holder["password"])
    visitor_headers = await auth_headers(client, visitor["email"], visitor["password"])
    await _visit(client, holder_headers, place, _phone())
    await raw_conn.execute("UPDATE property_locks SET expires_at = now() - interval '1 minute'")
    await raw_conn.execute("UPDATE lead_locks SET expires_at = now() - interval '1 minute'")
    await raw_conn.execute("UPDATE opportunities SET expires_at = now() - interval '1 minute'")

    taken = await _visit(client, visitor_headers, place, _phone())
    assert taken["waitlisted"] is False
    assert taken["opportunity"]["status"] == "NEW"


async def test_plotless_opportunities_go_through_every_status_without_500(
    client, make_employee, make_project_and_plot
):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    for target in ("Interested", "Deal In Progress", "Deal Complete", "Deal Rejected", "Lost", "Release Lock"):
        place = await make_project_and_plot()
        opportunity = (await _visit(client, headers, place, _phone(), with_plot=False))["opportunity"]
        response = await _set(client, headers, opportunity["id"], target)
        assert response.status_code == 200, (target, response.text)
