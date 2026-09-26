"""Functional tests for the REQ-25 Opportunity Engine and Deal conversion (§16, §20.6).

Note: because the V1 lock engine (LEAD_LOCKED/PROPERTY_LOCKED) always rejects a
second employee before the opportunity conflict service ever runs, a genuine
ATTRIBUTION_CONFLICT between two *employees* is unreachable through this portal
by design — that path is reserved for Employee-vs-Channel-Partner races (§16.5).
We exercise that scenario by seeding a Channel-Partner-owned opportunity
directly (there is no Channel Partner submission API in this portal yet).
"""

from datetime import date, timedelta

from tests.conftest import auth_headers


def _visit_at(days_offset: int = 0) -> str:
    target = date.today() + timedelta(days=days_offset)
    return f"{target.isoformat()}T11:00:00+05:30"


async def test_site_visit_creates_active_opportunity_owned_by_employee(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    visit = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Opp Owner Test",
            "phone": "9811100001",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert visit.status_code == 200

    opportunities = await client.get("/api/v1/opportunities", headers=headers)
    assert opportunities.status_code == 200
    items = opportunities.json()["data"]
    assert len(items) == 1
    assert items[0]["source_owner_type"] == "EMPLOYEE"
    assert items[0]["status"] == "NEW"
    assert items[0]["handling_employee_id"] == str(employee["id"])

    opportunity_id = items[0]["id"]
    assert visit.json()["data"]["opportunity"]["id"] == opportunity_id
    assert visit.json()["data"]["can_update_opportunity"] is True
    visits = await client.get("/api/v1/site-visits", headers=headers)
    assert visits.status_code == 200
    assert visits.json()["data"][0]["opportunity"]["id"] == opportunity_id
    detail = await client.get(f"/api/v1/opportunities/{opportunity_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["handling_employee_id"] == str(employee["id"])


async def test_channel_partner_source_survives_employee_handling(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    # Seed a Channel-Partner-owned Master Lead + Opportunity directly (no CP API exists yet).
    partner = await raw_conn.fetchrow(
        "INSERT INTO channel_partners (partner_code, name) VALUES ('CP-201', 'Test Partner') RETURNING id"
    )
    lead = await raw_conn.fetchrow(
        """
        INSERT INTO leads (name, normalized_phone, source, lifecycle_status)
        VALUES ('CP Sourced Lead', '+919811100002', 'CHANNEL_PARTNER', 'IN_PROGRESS')
        RETURNING id
        """
    )
    await raw_conn.execute(
        """
        INSERT INTO opportunities (
            lead_id, project_id, property_id, source_owner_type, source_owner_channel_partner_id,
            source, status, attribution_status, expires_at
        )
        VALUES ($1, $2, $3, 'CHANNEL_PARTNER', $4, 'CHANNEL_PARTNER', 'ACTIVE', 'VERIFIED', now() + interval '3 days')
        """,
        lead["id"],
        place["project_id"],
        place["property_id"],
        partner["id"],
    )

    # Employee now conducts the site visit for the SAME lead + plot.
    visit = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "CP Sourced Lead",
            "phone": "9811100002",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert visit.status_code == 200, visit.text

    opportunities = await client.get("/api/v1/opportunities", headers=headers)
    items = opportunities.json()["data"]
    assert len(items) == 1
    assert items[0]["source_owner_type"] == "CHANNEL_PARTNER"
    assert items[0]["status"] == "ACTIVE"
    assert items[0]["handling_employee_id"] == str(employee["id"])


async def test_resolve_conflict_requires_back_office_key(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    lead = await raw_conn.fetchrow(
        "INSERT INTO leads (name, normalized_phone) VALUES ('Conflict Lead', '+919811100003') RETURNING id"
    )
    opp = await raw_conn.fetchrow(
        """
        INSERT INTO opportunities (
            lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
            source, status, attribution_status, expires_at
        )
        VALUES ($1, $2, $3, 'EMPLOYEE', $4, 'EMPLOYEE_SITE_VISIT', 'ATTRIBUTION_CONFLICT', 'CONFLICT', now() + interval '3 days')
        RETURNING id
        """,
        lead["id"],
        place["project_id"],
        place["property_id"],
        employee["id"],
    )

    no_key_response = await client.post(
        f"/api/v1/opportunities/{opp['id']}/resolve",
        headers=headers,
        json={
            "resolved_source_owner_type": "EMPLOYEE",
            "resolved_source_owner_employee_id": str(employee["id"]),
            "reason": "back-office decision",
            "resolved_by": "ops-team",
        },
    )
    assert no_key_response.status_code == 403

    from app.core.config import get_settings

    ok_response = await client.post(
        f"/api/v1/opportunities/{opp['id']}/resolve",
        headers={**headers, "X-Back-Office-Key": get_settings().back_office_api_key},
        json={
            "resolved_source_owner_type": "EMPLOYEE",
            "resolved_source_owner_employee_id": str(employee["id"]),
            "reason": "back-office decision",
            "resolved_by": "ops-team",
        },
    )
    assert ok_response.status_code == 200, ok_response.text
    assert ok_response.json()["data"]["status"] == "ACTIVE"
    assert ok_response.json()["data"]["attribution_status"] == "RESOLVED"


async def test_opportunity_not_found_returns_404(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get(
        "/api/v1/opportunities/00000000-0000-0000-0000-000000000000", headers=headers
    )
    assert response.status_code == 404


async def test_deal_lifecycle_create_complete(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    visit = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Deal Customer",
            "phone": "9811100004",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    assert visit.status_code == 200
    opportunities = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"]
    opportunity_id = opportunities[0]["id"]

    deal_response = await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})
    assert deal_response.status_code == 200, deal_response.text
    deal = deal_response.json()["data"]
    assert deal["status"] == "CONFIRMED"

    inventory = await client.get(
        "/api/v1/properties/inventory", headers=headers, params={"project_id": place["project_id"]}
    )
    assert inventory.json()["data"][0]["status"] == "DEAL_LOCKED"

    # A second deal attempt on the same (now CONVERTED) opportunity must fail.
    second_deal = await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})
    assert second_deal.status_code == 409
    assert second_deal.json()["error"]["code"] == "OPPORTUNITY_NOT_ACTIVE"

    complete_response = await client.post(f"/api/v1/deals/{deal['id']}/complete", headers=headers)
    assert complete_response.status_code == 200
    assert complete_response.json()["data"]["status"] == "COMPLETED"

    inventory_after = await client.get(
        "/api/v1/properties/inventory", headers=headers, params={"project_id": place["project_id"]}
    )
    assert inventory_after.json()["data"][0]["status"] == "SOLD"


async def test_deal_cancel_releases_property(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Cancel Deal Customer",
            "phone": "9811100005",
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": _visit_at(),
        },
    )
    opportunity_id = (await client.get("/api/v1/opportunities", headers=headers)).json()["data"][0]["id"]
    deal = (await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": opportunity_id})).json()["data"]

    cancel_response = await client.post(f"/api/v1/deals/{deal['id']}/cancel", headers=headers)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["data"]["status"] == "CANCELLED"

    inventory = await client.get(
        "/api/v1/properties/inventory", headers=headers, params={"project_id": place["project_id"]}
    )
    assert inventory.json()["data"][0]["status"] == "AVAILABLE"


async def test_deal_requires_property_specific_opportunity(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    lead = await raw_conn.fetchrow(
        "INSERT INTO leads (name, normalized_phone) VALUES ('No Plot Lead', '+919811100006') RETURNING id"
    )
    opp = await raw_conn.fetchrow(
        """
        INSERT INTO opportunities (
            lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
            source, status, attribution_status, expires_at
        )
        VALUES ($1, $2, NULL, 'EMPLOYEE', $3, 'EMPLOYEE_SITE_VISIT', 'ACTIVE', 'VERIFIED', now() + interval '3 days')
        RETURNING id
        """,
        lead["id"],
        place["project_id"],
        employee["id"],
    )

    response = await client.post("/api/v1/deals", headers=headers, json={"opportunity_id": str(opp["id"])})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
