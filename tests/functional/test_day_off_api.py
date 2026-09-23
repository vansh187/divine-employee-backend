"""Functional tests for /api/v1/day-off/* (§7-8)."""

from datetime import date, timedelta

from tests.conftest import auth_headers


def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


async def test_select_day_off_freezes_immediately(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    response = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": _future_date(3)})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "FROZEN"
    assert data["frozen_at"] is not None


async def test_current_week_reflects_frozen_selection(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    # Must stay within *this* ISO week (Mon-Sun) — a couple of days out can
    # roll into next week depending on what day "today" is.
    target = date.today().isoformat()

    await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": target})
    response = await client.get("/api/v1/day-off/current-week", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["day_off_date"] == target


async def test_current_week_null_when_nothing_selected(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/day-off/current-week", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] is None


async def test_cannot_freeze_second_date_same_week(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    # Start from a Monday comfortably in the future so both dates land in the
    # same ISO week regardless of what "today" happens to be.
    anchor = date.today() + timedelta(days=14)
    monday = anchor - timedelta(days=anchor.weekday())
    tuesday = monday + timedelta(days=1)
    assert monday.isocalendar()[:2] == tuesday.isocalendar()[:2]

    first_resp = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": monday.isoformat()})
    assert first_resp.status_code == 200

    second_resp = await client.post(
        "/api/v1/day-off/select", headers=headers, json={"day_off_date": tuesday.isoformat()}
    )
    assert second_resp.status_code == 409
    assert second_resp.json()["error"]["code"] == "DAY_OFF_ALLOWANCE_EXCEEDED"


async def test_refreezing_same_date_is_idempotent(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    target = _future_date(4)

    first = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": target})
    second = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": target})
    assert first.status_code == 200
    assert second.status_code == 200


async def test_cannot_freeze_day_off_with_existing_site_visit(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    target = _future_date(6)

    visit_response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Pre-existing Visit",
            "phone": "9812349999",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": f"{target}T11:00:00+05:30",
        },
    )
    assert visit_response.status_code == 200

    day_off_response = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": target})
    assert day_off_response.status_code == 409
    assert day_off_response.json()["error"]["code"] == "DAY_OFF_VISIT_EXISTS"


async def test_day_off_requires_valid_date_format(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": "not-a-date"})
    assert response.status_code == 422


async def test_day_off_history_is_paginated(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": _future_date(1)})
    response = await client.get("/api/v1/day-off/history", headers=headers, params={"page": 1, "page_size": 5})
    assert response.status_code == 200
    assert "pagination" in response.json()
