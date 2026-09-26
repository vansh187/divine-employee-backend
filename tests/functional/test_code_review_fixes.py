"""Functional regression tests for the second code-review pass: cross-employee
access to site visits/opportunities/deal creation, conflict-resolution state
checks, per-employee idempotency keys, and request validation.
"""

from datetime import date, timedelta

from app.core.config import get_settings
from tests.conftest import auth_headers


def _visit_at(days_offset: int = 0, hour: int = 11) -> str:
    target = date.today() + timedelta(days=days_offset)
    return f"{target.isoformat()}T{hour:02d}:00:00+05:30"


async def _log_visit(client, headers, place, phone: str, **extra) -> dict:
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Review Fix Visitor",
            "phone": phone,
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_employee_cannot_view_another_employees_site_visit(client, make_employee, make_project_and_plot):
    owner = await make_employee()
    intruder = await make_employee()
    place = await make_project_and_plot()
    owner_headers = await auth_headers(client, owner["email"], owner["password"])
    intruder_headers = await auth_headers(client, intruder["email"], intruder["password"])

    visit = await _log_visit(client, owner_headers, place, "9833300001")

    assert (await client.get(f"/api/v1/site-visits/{visit['id']}", headers=owner_headers)).status_code == 200
    intruder_view = await client.get(f"/api/v1/site-visits/{visit['id']}", headers=intruder_headers)
    assert intruder_view.status_code == 403


async def test_unknown_site_visit_returns_404(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/site-visits/00000000-0000-0000-0000-000000000000", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_employee_cannot_view_or_convert_another_employees_opportunity(
    client, make_employee, make_project_and_plot
):
    owner = await make_employee()
    intruder = await make_employee()
    place = await make_project_and_plot()
    owner_headers = await auth_headers(client, owner["email"], owner["password"])
    intruder_headers = await auth_headers(client, intruder["email"], intruder["password"])

    await _log_visit(client, owner_headers, place, "9833300002")
    opportunity_id = (await client.get("/api/v1/opportunities", headers=owner_headers)).json()["data"][0]["id"]

    assert (await client.get(f"/api/v1/opportunities/{opportunity_id}", headers=intruder_headers)).status_code == 403
    assert (
        await client.get(f"/api/v1/opportunities/{opportunity_id}/claims", headers=intruder_headers)
    ).status_code == 403

    intruder_deal = await client.post("/api/v1/deals", headers=intruder_headers, json={"opportunity_id": opportunity_id})
    assert intruder_deal.status_code == 403

    inventory = await client.get(
        "/api/v1/properties/inventory", headers=owner_headers, params={"project_id": place["project_id"]}
    )
    assert inventory.json()["data"][0]["status"] == "LOCKED"


async def test_resolve_rejects_opportunity_not_in_conflict(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    await _log_visit(client, headers, place, "9833300003")
    opportunity_id = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"][0]["id"]
    await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})

    response = await client.post(
        f"/api/v1/opportunities/{opportunity_id}/resolve",
        headers={"X-Back-Office-Key": get_settings().back_office_api_key},
        json={
            "resolved_source_owner_type": "EMPLOYEE",
            "resolved_source_owner_employee_id": str(employee["id"]),
            "reason": "should not revive a converted opportunity",
            "resolved_by": "ops-team",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPPORTUNITY_NOT_IN_CONFLICT"


async def test_resolve_rejects_inconsistent_owner_reference(client):
    response = await client.post(
        "/api/v1/opportunities/00000000-0000-0000-0000-000000000000/resolve",
        headers={"X-Back-Office-Key": get_settings().back_office_api_key},
        json={"resolved_source_owner_type": "EMPLOYEE", "reason": "missing employee id", "resolved_by": "ops-team"},
    )
    assert response.status_code == 422


async def test_idempotency_key_is_scoped_per_employee(client, make_employee, make_project_and_plot):
    employee_1 = await make_employee()
    employee_2 = await make_employee()
    place_1 = await make_project_and_plot()
    place_2 = await make_project_and_plot()
    headers_1 = await auth_headers(client, employee_1["email"], employee_1["password"])
    headers_2 = await auth_headers(client, employee_2["email"], employee_2["password"])

    visit_1 = await _log_visit(client, headers_1, place_1, "9833300004", idempotency_key="shared-key")
    visit_2 = await _log_visit(client, headers_2, place_2, "9833300005", idempotency_key="shared-key")

    assert visit_1["id"] != visit_2["id"]
    assert visit_2["employee_id"] == str(employee_2["id"])


async def test_unknown_outcome_returns_422(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Bad Outcome",
            "phone": "9833300006",
            "project_id": place["project_id"],
            "visit_at": _visit_at(),
            "outcome": "MAYBE",
        },
    )
    assert response.status_code == 422


async def test_uppercase_ids_are_normalized(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    visit = await _log_visit(
        client,
        headers,
        {"project_id": place["project_id"].upper(), "property_id": place["property_id"].upper()},
        "9833300007",
    )
    assert visit["project_id"] == place["project_id"]


async def test_opportunity_expiry_ignores_backdated_visit_at(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    await _log_visit(client, headers, place, "9833300008", visit_at=_visit_at(days_offset=-10))
    opportunity = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"][0]
    assert opportunity["status"] == "NEW"
    assert date.fromisoformat(opportunity["expires_at"][:10]) >= date.today()
