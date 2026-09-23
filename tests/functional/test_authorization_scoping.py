"""Functional tests for cross-employee authorization scoping (fixed after code review):
deals and leads must not be readable/actionable by an employee who doesn't own them,
and a deal can't be completed/cancelled twice.
"""

from datetime import date, timedelta

from tests.conftest import auth_headers


def _visit_at(days_offset: int = 0) -> str:
    target = date.today() + timedelta(days=days_offset)
    return f"{target.isoformat()}T11:00:00+05:30"


async def test_employee_cannot_view_another_employees_lead(client, make_employee, make_project_and_plot):
    owner = await make_employee()
    intruder = await make_employee()
    place = await make_project_and_plot()
    owner_headers = await auth_headers(client, owner["email"], owner["password"])
    intruder_headers = await auth_headers(client, intruder["email"], intruder["password"])

    visit = await client.post(
        "/api/v1/site-visits",
        headers=owner_headers,
        json={
            "visitor_name": "Private Lead",
            "phone": "9822200001",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    lead_id = visit.json()["data"]["lead_id"]

    owner_view = await client.get(f"/api/v1/leads/{lead_id}", headers=owner_headers)
    assert owner_view.status_code == 200

    intruder_view = await client.get(f"/api/v1/leads/{lead_id}", headers=intruder_headers)
    assert intruder_view.status_code == 403
    assert intruder_view.json()["error"]["code"] == "FORBIDDEN"

    intruder_follow_ups = await client.get(f"/api/v1/leads/{lead_id}/follow-ups", headers=intruder_headers)
    assert intruder_follow_ups.status_code == 403


async def test_employee_cannot_complete_or_cancel_another_employees_deal(client, make_employee, make_project_and_plot):
    owner = await make_employee()
    intruder = await make_employee()
    place = await make_project_and_plot()
    owner_headers = await auth_headers(client, owner["email"], owner["password"])
    intruder_headers = await auth_headers(client, intruder["email"], intruder["password"])

    await client.post(
        "/api/v1/site-visits",
        headers=owner_headers,
        json={
            "visitor_name": "Deal Owner Test",
            "phone": "9822200002",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    opportunity_id = (await client.get("/api/v1/opportunities", headers=owner_headers)).json()["data"][0]["id"]
    deal = (
        await client.post("/api/v1/deals", headers=owner_headers, json={"opportunity_id": opportunity_id})
    ).json()["data"]

    intruder_get = await client.get(f"/api/v1/deals/{deal['id']}", headers=intruder_headers)
    assert intruder_get.status_code == 403

    intruder_complete = await client.post(f"/api/v1/deals/{deal['id']}/complete", headers=intruder_headers)
    assert intruder_complete.status_code == 403
    assert intruder_complete.json()["error"]["code"] == "FORBIDDEN"

    intruder_cancel = await client.post(f"/api/v1/deals/{deal['id']}/cancel", headers=intruder_headers)
    assert intruder_cancel.status_code == 403

    # Owner can still legitimately complete their own deal.
    owner_complete = await client.post(f"/api/v1/deals/{deal['id']}/complete", headers=owner_headers)
    assert owner_complete.status_code == 200


async def test_cannot_cancel_an_already_completed_deal(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "State Machine Test",
            "phone": "9822200003",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    opportunity_id = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"][0]["id"]
    deal = (await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})).json()["data"]

    complete_response = await client.post(f"/api/v1/deals/{deal['id']}/complete", headers=headers)
    assert complete_response.status_code == 200

    # Deal is now COMPLETED — cancelling it afterwards must be rejected, not silently
    # flip the property back to AVAILABLE after it was already sold.
    cancel_response = await client.post(f"/api/v1/deals/{deal['id']}/cancel", headers=headers)
    assert cancel_response.status_code == 409
    assert cancel_response.json()["error"]["code"] == "DEAL_NOT_CONFIRMED"

    inventory = await client.get(
        "/api/v1/properties/inventory", headers=headers, params={"project_id": place["project_id"]}
    )
    assert inventory.json()["data"][0]["status"] == "SOLD"


async def test_cannot_complete_an_already_cancelled_deal(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "State Machine Test 2",
            "phone": "9822200004",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    opportunity_id = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"][0]["id"]
    deal = (await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})).json()["data"]

    cancel_response = await client.post(f"/api/v1/deals/{deal['id']}/cancel", headers=headers)
    assert cancel_response.status_code == 200

    complete_response = await client.post(f"/api/v1/deals/{deal['id']}/complete", headers=headers)
    assert complete_response.status_code == 409
    assert complete_response.json()["error"]["code"] == "DEAL_NOT_CONFIRMED"
