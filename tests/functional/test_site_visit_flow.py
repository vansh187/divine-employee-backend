"""Functional tests for Site Visit -> Lead resolution -> Lock engine (§3-5, §11).

These are the highest-risk business rules in the system: lead dedup by phone,
day-off blocking, and the deterministic LEAD_LOCKED/PROPERTY_LOCKED conflicts.
"""

from datetime import date, timedelta

from tests.conftest import auth_headers


def _visit_at(days_offset: int = 0, hour: int = 11) -> str:
    target = date.today() + timedelta(days=days_offset)
    return f"{target.isoformat()}T{hour:02d}:00:00+05:30"


async def test_create_site_visit_creates_lead_and_locks(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Rahul Sharma",
            "phone": "9812345001",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
            "outcome": "INTERESTED",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["lead_name"] == "Rahul Sharma"
    assert body["plot_no"] is not None

    locks_response = await client.get("/api/v1/leads/active-locks", headers=headers)
    assert len(locks_response.json()["data"]) == 1

    inventory_response = await client.get(
        "/api/v1/properties/inventory", headers=headers, params={"project_id": place["project_id"]}
    )
    assert inventory_response.json()["data"][0]["status"] == "LOCKED"


async def test_existing_lead_is_reused_by_normalized_phone(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place1 = await make_project_and_plot()
    place2 = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    first = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Anita Rao",
            "phone": "9812345002",
            "project_id": place1["project_id"],
            "property_id": place1["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert first.status_code == 200
    lead_id_1 = first.json()["data"]["lead_id"]

    # Same person, differently formatted phone number, different plot/day.
    second = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Anita Rao",
            "phone": "+91 98123 45002",
            "project_id": place2["project_id"],
            "property_id": place2["property_id"],
            "visit_at": _visit_at(days_offset=0, hour=15),
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["lead_id"] == lead_id_1


async def test_frozen_day_off_blocks_site_visit(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    freeze_date = date.today() + timedelta(days=5)
    freeze_response = await client.post(
        "/api/v1/day-off/select", headers=headers, json={"day_off_date": freeze_date.isoformat()}
    )
    assert freeze_response.status_code == 200

    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Blocked Visitor",
            "phone": "9812345003",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": f"{freeze_date.isoformat()}T11:00:00+05:30",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DAY_OFF_CONFLICT"


async def test_second_employee_visit_recorded_when_lead_locked(client, make_employee, make_project_and_plot):
    employee_1 = await make_employee()
    employee_2 = await make_employee()
    place = await make_project_and_plot()
    headers_1 = await auth_headers(client, employee_1["email"], employee_1["password"])
    headers_2 = await auth_headers(client, employee_2["email"], employee_2["password"])

    first = await client.post(
        "/api/v1/site-visits",
        headers=headers_1,
        json={
            "visitor_name": "Contested Lead",
            "phone": "9812345004",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert first.status_code == 200

    conflict = await client.post(
        "/api/v1/site-visits",
        headers=headers_2,
        json={
            "visitor_name": "Contested Lead",
            "phone": "9812345004",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(hour=14),
        },
    )
    # The visit is still recorded, but it takes no lock and creates no opportunity.
    assert conflict.status_code == 200, conflict.text
    assert conflict.json()["data"]["lead_held"] is True
    assert conflict.json()["data"]["opportunity"] is None
    assert conflict.json()["data"]["can_update_opportunity"] is False


async def test_second_employee_waitlisted_when_plot_held_different_lead(client, make_employee, make_project_and_plot):
    employee_1 = await make_employee()
    employee_2 = await make_employee()
    place = await make_project_and_plot()
    headers_1 = await auth_headers(client, employee_1["email"], employee_1["password"])
    headers_2 = await auth_headers(client, employee_2["email"], employee_2["password"])

    first = await client.post(
        "/api/v1/site-visits",
        headers=headers_1,
        json={
            "visitor_name": "First Customer",
            "phone": "9812345005",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert first.status_code == 200

    conflict = await client.post(
        "/api/v1/site-visits",
        headers=headers_2,
        json={
            "visitor_name": "Different Customer",
            "phone": "9812345006",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(hour=16),
        },
    )
    # The visit is still recorded and the customer waits for the plot.
    assert conflict.status_code == 200, conflict.text
    assert conflict.json()["data"]["waitlisted"] is True
    assert conflict.json()["data"]["opportunity"] is None


async def test_same_employee_revisit_renews_lock_without_conflict(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    first = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Repeat Visitor",
            "phone": "9812345007",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Repeat Visitor",
            "phone": "9812345007",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(hour=17),
        },
    )
    assert second.status_code == 200
    locks_response = await client.get("/api/v1/leads/active-locks", headers=headers)
    assert len(locks_response.json()["data"]) == 1


async def test_idempotency_key_prevents_duplicate_visit_on_retry(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    payload = {
        "visitor_name": "Idempotent Visitor",
        "phone": "9812345008",
        "project_id": place["project_id"],
        "property_id": place["property_id"],
        "visit_at": _visit_at(),
        "idempotency_key": "retry-key-123",
    }

    first = await client.post("/api/v1/site-visits", headers=headers, json=payload)
    second = await client.post("/api/v1/site-visits", headers=headers, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]

    list_response = await client.get("/api/v1/site-visits", headers=headers)
    assert list_response.json()["pagination"]["total_items"] == 1


async def test_missing_required_fields_returns_422(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post("/api/v1/site-visits", headers=headers, json={"visitor_name": "No Phone"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_invalid_phone_returns_validation_failed_not_500(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Bad Phone",
            "phone": "abc12345",
            "project_id": place["project_id"],
            "visit_at": _visit_at(),
        },
    )
    assert response.status_code in (400, 422)


async def test_nonexistent_project_id_returns_404_not_500(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Ghost Project",
            "phone": "9812345009",
            "project_id": "00000000-0000-0000-0000-000000000000",
            "visit_at": _visit_at(),
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_malformed_uuid_path_param_does_not_crash(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/site-visits/not-a-uuid", headers=headers)
    assert response.status_code < 500
