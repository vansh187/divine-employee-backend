"""Functional tests for POST /api/v1/opportunities/{id}/status (employee-driven CONVERTED / LOST / RELEASED)."""

import uuid
from datetime import date

from tests.conftest import auth_headers


async def _log_visit(client, headers, place, phone: str, with_plot: bool = True) -> dict:
    payload = {
        "visitor_name": "Status Test Lead",
        "phone": phone,
        "project_id": place["project_id"],
        "visit_at": f"{date.today().isoformat()}T11:00:00+05:30",
    }
    if with_plot:
        payload["property_id"] = place["property_id"]
    response = await client.post("/api/v1/site-visits", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    opportunities = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"]
    assert len(opportunities) == 1
    return opportunities[0]


def _phone() -> str:
    return "9" + str(uuid.uuid4().int)[:9]


async def test_lost_updates_status_frees_plot_and_notifies(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone())
    assert await raw_conn.fetchval("SELECT status FROM properties WHERE id = $1", uuid.UUID(place["property_id"])) == "LOCKED"

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "LOST"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "LOST"
    assert body["data"]["id"] == opportunity["id"]

    assert await raw_conn.fetchval("SELECT status FROM properties WHERE id = $1", uuid.UUID(place["property_id"])) == "AVAILABLE"
    assert await raw_conn.fetchval(
        "SELECT status FROM property_locks WHERE property_id = $1", uuid.UUID(place["property_id"])
    ) == "RELEASED"

    notifications = await raw_conn.fetch(
        "SELECT event_type, entity_type FROM notifications WHERE entity_id = $1 AND event_type = 'OPPORTUNITY_RESOLVED'",
        uuid.UUID(opportunity["id"]),
    )
    assert len(notifications) == 1
    assert notifications[0]["entity_type"] == "OPPORTUNITY"

    detail = await client.get(f"/api/v1/leads/{opportunity['lead_id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert [o["status"] for o in detail.json()["data"]["opportunities"]] == ["LOST"]

    again = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "RELEASED"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "OPPORTUNITY_NOT_ACTIVE"


async def test_released_with_plot_frees_plot(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone())

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "RELEASED"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "RELEASED"
    assert await raw_conn.fetchval("SELECT status FROM properties WHERE id = $1", uuid.UUID(place["property_id"])) == "AVAILABLE"


async def test_converted_with_plot_creates_deal_and_locks_plot(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone())

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "CONVERTED"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "CONVERTED"

    deals = (await client.get("/api/v1/deals", headers=headers)).json()["data"]
    assert [d["opportunity_id"] for d in deals] == [opportunity["id"]]
    assert deals[0]["status"] == "CONFIRMED"
    assert await raw_conn.fetchval("SELECT status FROM properties WHERE id = $1", uuid.UUID(place["property_id"])) == "DEAL_LOCKED"
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM notifications WHERE entity_id = $1 AND event_type = 'DEAL_LOCKED'",
        uuid.UUID(opportunity["id"]),
    ) == 1


async def test_converted_without_plot_only_changes_status(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone(), with_plot=False)
    assert opportunity["property_id"] is None

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "CONVERTED"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "CONVERTED"
    assert (await client.get("/api/v1/deals", headers=headers)).json()["data"] == []


async def test_lost_without_plot_works(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone(), with_plot=False)

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "LOST"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "LOST"


async def test_other_employee_is_forbidden(client, make_employee, make_project_and_plot):
    owner = await make_employee()
    outsider = await make_employee()
    place = await make_project_and_plot()
    owner_headers = await auth_headers(client, owner["email"], owner["password"])
    outsider_headers = await auth_headers(client, outsider["email"], outsider["password"])
    opportunity = await _log_visit(client, owner_headers, place, _phone())

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=outsider_headers, json={"status": "LOST"})
    assert response.status_code == 403

    still_active = await client.get(f"/api/v1/opportunities/{opportunity['id']}", headers=owner_headers)
    assert still_active.json()["data"]["status"] == "NEW"


async def test_expired_opportunity_cannot_be_updated(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone())
    await raw_conn.execute(
        "UPDATE opportunities SET expires_at = now() - interval '1 hour' WHERE id = $1", uuid.UUID(opportunity["id"])
    )

    response = await client.post(f"/api/v1/opportunities/{opportunity['id']}/status", headers=headers, json={"status": "CONVERTED"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPPORTUNITY_NOT_ACTIVE"


async def test_ui_dropdown_labels_are_accepted(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone())
    url = f"/api/v1/opportunities/{opportunity['id']}/status"

    in_progress = await client.post(url, headers=headers, json={"status": "Deal In Progress"})
    assert in_progress.status_code == 200, in_progress.text
    assert in_progress.json()["data"]["status"] == "DEAL_IN_PROGRESS"

    rejected = await client.post(url, headers=headers, json={"status": "Deal Rejected"})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "DEAL_REJECTED"


async def test_deal_complete_and_release_lock_labels(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    first = await _log_visit(client, headers, await make_project_and_plot(), _phone())
    done = await client.post(
        f"/api/v1/opportunities/{first['id']}/status", headers=headers, json={"status": "deal complete"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["status"] == "CONVERTED"


async def test_invalid_status_unknown_id_and_missing_auth(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    opportunity = await _log_visit(client, headers, place, _phone())
    url = f"/api/v1/opportunities/{opportunity['id']}/status"

    for bad_status in ("EXPIRED", "ATTRIBUTION_CONFLICT", "Deal Maybe", ""):
        response = await client.post(url, headers=headers, json={"status": bad_status})
        assert response.status_code == 422, (bad_status, response.text)
    assert (await client.post(url, headers=headers, json={})).status_code == 422
    assert (await client.post(url, headers=headers)).status_code == 422

    missing = await client.post(
        "/api/v1/opportunities/00000000-0000-0000-0000-000000000000/status", headers=headers, json={"status": "LOST"}
    )
    assert missing.status_code == 404
    assert (await client.post("/api/v1/opportunities/not-a-uuid/status", headers=headers, json={"status": "LOST"})).status_code == 422
    assert (await client.post(url, json={"status": "LOST"})).status_code in (401, 403)

    unchanged = await client.get(f"/api/v1/opportunities/{opportunity['id']}", headers=headers)
    assert unchanged.json()["data"]["status"] == "NEW"
