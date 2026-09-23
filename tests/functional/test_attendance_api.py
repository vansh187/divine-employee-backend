"""Functional tests for /api/v1/attendance/* (§6)."""

from datetime import date, timedelta

from tests.conftest import auth_headers


async def test_check_in_then_check_out(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    check_in = await client.post("/api/v1/attendance/check-in", headers=headers, json={})
    assert check_in.status_code == 200
    data = check_in.json()["data"]
    assert data["check_in_at"] is not None
    assert data["status"] in ("PRESENT", "LATE")

    check_out = await client.post("/api/v1/attendance/check-out", headers=headers)
    assert check_out.status_code == 200
    assert check_out.json()["data"]["check_out_at"] is not None


async def test_double_check_in_is_rejected(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    first = await client.post("/api/v1/attendance/check-in", headers=headers, json={})
    assert first.status_code == 200

    second = await client.post("/api/v1/attendance/check-in", headers=headers, json={})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ALREADY_CHECKED_IN"


async def test_check_out_without_check_in_is_rejected(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    response = await client.post("/api/v1/attendance/check-out", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_double_check_out_is_rejected(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    await client.post("/api/v1/attendance/check-in", headers=headers, json={})
    await client.post("/api/v1/attendance/check-out", headers=headers)
    second_checkout = await client.post("/api/v1/attendance/check-out", headers=headers)
    assert second_checkout.status_code == 409
    assert second_checkout.json()["error"]["code"] == "ALREADY_CHECKED_OUT"


async def test_check_in_blocked_on_frozen_day_off(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    today = date.today().isoformat()
    freeze = await client.post("/api/v1/day-off/select", headers=headers, json={"day_off_date": today})
    assert freeze.status_code == 200

    response = await client.post("/api/v1/attendance/check-in", headers=headers, json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DAY_OFF_CONFLICT"


async def test_today_endpoint_returns_null_before_check_in(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/attendance/today", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] is None


async def test_calendar_rejects_invalid_month(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/attendance/calendar", headers=headers, params={"year": 2026, "month": 13})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_calendar_missing_query_params_returns_422_not_500(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/attendance/calendar", headers=headers)
    assert response.status_code == 422
