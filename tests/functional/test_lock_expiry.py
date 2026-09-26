"""Functional tests for lock expiry on serverless hosts: lapsed locks must not
block anyone even if no sweep has run, and the scheduler-only sweep endpoint.
"""

from datetime import date, timedelta

from app.core.config import get_settings
from app.main import app
from tests.conftest import auth_headers

_CRON_SECRET = "c" * 40


def _visit_at(hour: int = 11) -> str:
    return f"{date.today().isoformat()}T{hour:02d}:00:00+05:30"


async def _log_visit(client, headers, place, phone: str, hour: int = 11):
    return await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Expiry Visitor",
            "phone": phone,
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(hour),
        },
    )


async def _lapse_all_protection(raw_conn, property_id: str) -> None:
    """Simulates 3+ days passing with no sweep having run."""
    await raw_conn.execute(
        "UPDATE property_locks SET expires_at = now() - interval '1 minute' WHERE property_id = $1", property_id
    )
    await raw_conn.execute(
        """
        UPDATE lead_locks SET expires_at = now() - interval '1 minute'
        WHERE id IN (SELECT lead_lock_id FROM property_locks WHERE property_id = $1)
        """,
        property_id,
    )
    await raw_conn.execute(
        "UPDATE opportunities SET expires_at = now() - interval '1 minute' WHERE property_id = $1", property_id
    )


async def test_lapsed_locks_do_not_block_without_a_sweep(client, make_employee, make_project_and_plot, raw_conn):
    employee_1 = await make_employee()
    employee_2 = await make_employee()
    place = await make_project_and_plot()
    headers_1 = await auth_headers(client, employee_1["email"], employee_1["password"])
    headers_2 = await auth_headers(client, employee_2["email"], employee_2["password"])

    assert (await _log_visit(client, headers_1, place, "9844400001")).status_code == 200
    await _lapse_all_protection(raw_conn, place["property_id"])

    # Same lead + plot, different employee: allowed once protection has lapsed.
    second = await _log_visit(client, headers_2, place, "9844400001", hour=15)
    assert second.status_code == 200, second.text

    opportunities = (await client.get("/api/v1/opportunities", headers=headers_2)).json()["data"]
    assert len(opportunities) == 1
    assert opportunities[0]["status"] == "NEW"
    assert opportunities[0]["source_owner_employee_id"] == str(employee_2["id"])

    # The first employee no longer sees the lapsed lock as theirs.
    assert (await client.get("/api/v1/leads/active-locks", headers=headers_1)).json()["data"] == []


async def test_lapsed_opportunity_cannot_become_a_deal(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    assert (await _log_visit(client, headers, place, "9844400002")).status_code == 200
    opportunity_id = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"][0]["id"]
    await _lapse_all_protection(raw_conn, place["property_id"])

    response = await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPPORTUNITY_NOT_ACTIVE"


async def test_sweep_endpoint_requires_cron_secret(client):
    response = await client.post("/api/v1/internal/sweep", headers={"X-Cron-Secret": _CRON_SECRET})
    # CRON_SECRET is not configured in the test settings, so the endpoint is disabled.
    assert response.status_code == 403


async def test_sweep_endpoint_expires_lapsed_locks(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    assert (await _log_visit(client, headers, place, "9844400003")).status_code == 200
    await _lapse_all_protection(raw_conn, place["property_id"])

    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"cron_secret": _CRON_SECRET})
    try:
        wrong = await client.post("/api/v1/internal/sweep", headers={"X-Cron-Secret": "wrong"})
        assert wrong.status_code == 403

        response = await client.post("/api/v1/internal/sweep", headers={"X-Cron-Secret": _CRON_SECRET})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["expired_lead_locks"] >= 1
        assert data["expired_property_locks"] >= 1
        assert data["expired_opportunities"] >= 1

        # Plain-URL form for simple schedulers: GET with ?key=.
        assert (await client.get("/api/v1/internal/sweep", params={"key": _CRON_SECRET})).status_code == 200
        assert (await client.get("/api/v1/internal/sweep", params={"key": "wrong"})).status_code == 403
    finally:
        app.dependency_overrides.pop(get_settings, None)

    inventory = await client.get(
        "/api/v1/properties/inventory", headers=headers, params={"project_id": place["project_id"]}
    )
    assert inventory.json()["data"][0]["status"] == "AVAILABLE"
