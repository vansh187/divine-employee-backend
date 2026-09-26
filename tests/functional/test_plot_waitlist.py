"""A visit to a lead/plot held by another employee is recorded (waitlist), and a booking in progress keeps its hold."""

import uuid
from datetime import date

from tests.conftest import auth_headers


def _phone() -> str:
    return "9" + str(uuid.uuid4().int)[:9]


async def _visit(client, headers, place, phone: str, name: str = "Waitlist Lead") -> dict:
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": name,
            "phone": phone,
            "project_id": place["project_id"],
            "property_id": place["property_id"],
            "visit_at": f"{date.today().isoformat()}T11:00:00+05:30",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_held_plot_records_visit_waitlists_and_notifies_holder(
    client, make_employee, make_project_and_plot, raw_conn
):
    holder = await make_employee()
    waiter = await make_employee()
    place = await make_project_and_plot()
    holder_headers = await auth_headers(client, holder["email"], holder["password"])
    waiter_headers = await auth_headers(client, waiter["email"], waiter["password"])
    first = await _visit(client, holder_headers, place, _phone(), "Holder Lead")
    assert first["waitlisted"] is False and first["opportunity"]["status"] == "NEW"

    second = await _visit(client, waiter_headers, place, _phone(), "Waiting Lead")
    assert second["waitlisted"] is True
    assert second["opportunity"] is None
    assert second["held_until"] is not None

    plot = uuid.UUID(place["property_id"])
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM plot_waitlist WHERE property_id = $1 AND status = 'WAITING'", plot
    ) == 1
    # One lock only (the holder's); the waiter's lead is protected so follow-ups still work.
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM property_locks WHERE property_id = $1 AND status = 'ACTIVE'", plot
    ) == 1
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM lead_locks WHERE employee_id = $1 AND status = 'ACTIVE'", waiter["id"]
    ) == 1
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM notifications WHERE employee_id = $1 AND event_type = 'PROPERTY_LOCKED'", holder["id"]
    ) == 1
    visits = (await client.get("/api/v1/site-visits", headers=waiter_headers)).json()["data"]
    assert len(visits) == 1
    # Waiting again does not queue twice.
    await _visit(client, waiter_headers, place, (await raw_conn.fetchval(
        "SELECT visitor_phone FROM site_visits WHERE employee_id = $1", waiter["id"]
    )), "Waiting Lead")
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM plot_waitlist WHERE property_id = $1 AND status = 'WAITING'", plot
    ) == 1


async def test_waiter_notified_when_holder_releases_and_can_then_take_the_plot(
    client, make_employee, make_project_and_plot, raw_conn
):
    holder = await make_employee()
    waiter = await make_employee()
    place = await make_project_and_plot()
    holder_headers = await auth_headers(client, holder["email"], holder["password"])
    waiter_headers = await auth_headers(client, waiter["email"], waiter["password"])
    first = await _visit(client, holder_headers, place, _phone(), "Holder Lead")
    waiter_phone = _phone()
    await _visit(client, waiter_headers, place, waiter_phone, "Waiting Lead")

    released = await client.post(
        f"/api/v1/opportunities/{first['opportunity']['id']}/status", headers=holder_headers, json={"status": "LOST"}
    )
    assert released.status_code == 200, released.text
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM notifications WHERE employee_id = $1 AND event_type = 'PROPERTY_LOCK_RELEASED'",
        waiter["id"],
    ) == 1
    assert await raw_conn.fetchval(
        "SELECT status FROM plot_waitlist WHERE employee_id = $1", waiter["id"]
    ) == "PROMOTED"

    taken = await _visit(client, waiter_headers, place, waiter_phone, "Waiting Lead")
    assert taken["waitlisted"] is False
    assert taken["opportunity"]["status"] == "NEW"
    assert await raw_conn.fetchval(
        "SELECT employee_id FROM property_locks WHERE property_id = $1 AND status = 'ACTIVE'",
        uuid.UUID(place["property_id"]),
    ) == waiter["id"]


async def test_lead_held_by_other_employee_still_records_visit_without_lock(
    client, make_employee, make_project_and_plot, raw_conn
):
    owner = await make_employee()
    other = await make_employee()
    place = await make_project_and_plot()
    owner_headers = await auth_headers(client, owner["email"], owner["password"])
    other_headers = await auth_headers(client, other["email"], other["password"])
    phone = _phone()
    await _visit(client, owner_headers, place, phone)

    result = await _visit(client, other_headers, place, phone)
    assert result["lead_held"] is True and result["opportunity"] is None
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM lead_locks WHERE status = 'ACTIVE' AND employee_id = $1", other["id"]
    ) == 0
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM notifications WHERE employee_id = $1 AND event_type = 'LEAD_LOCKED'", owner["id"]
    ) == 1
    # The lead stays with its owner.
    assert await raw_conn.fetchval(
        "SELECT current_employee_id FROM leads WHERE normalized_phone LIKE '%' || right($1, 10)", phone
    ) == owner["id"]


async def test_deal_in_progress_extends_locks_and_opportunity_and_followups_never_shorten(
    client, make_employee, make_project_and_plot, raw_conn
):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    phone = _phone()
    visit = await _visit(client, headers, place, phone)
    opportunity_id = visit["opportunity"]["id"]
    plot = uuid.UUID(place["property_id"])

    response = await client.post(
        f"/api/v1/opportunities/{opportunity_id}/status", headers=headers, json={"status": "Deal In Progress"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "DEAL_IN_PROGRESS"

    days = await raw_conn.fetchrow(
        """
        SELECT extract(epoch FROM (o.expires_at - now())) / 86400 AS opportunity_days,
               extract(epoch FROM (pl.expires_at - now())) / 86400 AS plot_days,
               extract(epoch FROM (ll.expires_at - now())) / 86400 AS lead_days
        FROM opportunities o
        JOIN property_locks pl ON pl.property_id = $2 AND pl.status = 'ACTIVE'
        JOIN lead_locks ll ON ll.id = pl.lead_lock_id
        WHERE o.id = $1
        """,
        uuid.UUID(opportunity_id),
        plot,
    )
    assert all(14 < days[k] <= 15 for k in ("opportunity_days", "plot_days", "lead_days"))

    # A revisit renews with the 3-day window but must not shorten the longer hold.
    await _visit(client, headers, place, phone)
    after = await raw_conn.fetchval(
        "SELECT extract(epoch FROM (expires_at - now())) / 86400 FROM property_locks WHERE property_id = $1 AND status = 'ACTIVE'",
        plot,
    )
    assert after > 14


async def test_deal_in_progress_extends_only_its_own_plot(client, make_employee, make_project_and_plot, raw_conn):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    first_plot = await make_project_and_plot()
    second_plot = await make_project_and_plot()
    phone = _phone()
    first = await _visit(client, headers, first_plot, phone)
    await _visit(client, headers, second_plot, phone)

    response = await client.post(
        f"/api/v1/opportunities/{first['opportunity']['id']}/status", headers=headers, json={"status": "DEAL_IN_PROGRESS"}
    )
    assert response.status_code == 200, response.text

    async def plot_days(place: dict) -> float:
        return await raw_conn.fetchval(
            "SELECT extract(epoch FROM (expires_at - now())) / 86400 FROM property_locks "
            "WHERE property_id = $1 AND status = 'ACTIVE'",
            uuid.UUID(place["property_id"]),
        )

    assert await plot_days(first_plot) > 14
    assert await plot_days(second_plot) <= 3


async def test_stale_waiter_is_skipped_and_next_valid_waiter_is_promoted(
    client, make_employee, make_project_and_plot, raw_conn
):
    holder = await make_employee()
    stale = await make_employee()
    valid = await make_employee()
    place = await make_project_and_plot()
    holder_headers = await auth_headers(client, holder["email"], holder["password"])
    stale_headers = await auth_headers(client, stale["email"], stale["password"])
    valid_headers = await auth_headers(client, valid["email"], valid["password"])
    first = await _visit(client, holder_headers, place, _phone(), "Holder Lead")
    await _visit(client, stale_headers, place, _phone(), "Stale Lead")
    await _visit(client, valid_headers, place, _phone(), "Valid Lead")
    # The oldest waiter's own lead protection lapses.
    await raw_conn.execute(
        "UPDATE lead_locks SET expires_at = now() - interval '1 hour' WHERE employee_id = $1", stale["id"]
    )

    response = await client.post(
        f"/api/v1/opportunities/{first['opportunity']['id']}/status", headers=holder_headers, json={"status": "LOST"}
    )
    assert response.status_code == 200, response.text
    assert await raw_conn.fetchval("SELECT status FROM plot_waitlist WHERE employee_id = $1", stale["id"]) == "CANCELLED"
    assert await raw_conn.fetchval("SELECT status FROM plot_waitlist WHERE employee_id = $1", valid["id"]) == "PROMOTED"
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM notifications WHERE employee_id = $1 AND event_type = 'PROPERTY_LOCK_RELEASED'", stale["id"]
    ) == 0
    assert await raw_conn.fetchval(
        "SELECT count(*) FROM notifications WHERE employee_id = $1 AND event_type = 'PROPERTY_LOCK_RELEASED'", valid["id"]
    ) == 1


async def _visit_with_key(client, headers, place, phone: str, key: str, with_plot: bool = True) -> dict:
    payload = {
        "visitor_name": "Retry Lead",
        "phone": phone,
        "project_id": place["project_id"],
        "visit_at": f"{date.today().isoformat()}T11:00:00+05:30",
        "idempotency_key": key,
    }
    if with_plot:
        payload["property_id"] = place["property_id"]
    response = await client.post("/api/v1/site-visits", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_idempotent_retry_keeps_waitlisted_and_lead_held_flags(client, make_employee, make_project_and_plot):
    holder = await make_employee()
    other = await make_employee()
    place = await make_project_and_plot()
    holder_headers = await auth_headers(client, holder["email"], holder["password"])
    other_headers = await auth_headers(client, other["email"], other["password"])
    holder_phone = _phone()
    await _visit(client, holder_headers, place, holder_phone)

    waitlisted = await _visit_with_key(client, other_headers, place, _phone(), "key-waitlisted")
    assert waitlisted["waitlisted"] is True
    retry = await _visit_with_key(client, other_headers, place, _phone(), "key-waitlisted")
    assert retry["id"] == waitlisted["id"]
    assert retry["waitlisted"] is True and retry["lead_held"] is False and retry["held_until"] is not None

    held = await _visit_with_key(client, other_headers, place, holder_phone, "key-lead-held")
    assert held["lead_held"] is True
    retry_held = await _visit_with_key(client, other_headers, place, holder_phone, "key-lead-held")
    assert retry_held["id"] == held["id"]
    assert retry_held["lead_held"] is True and retry_held["waitlisted"] is False and retry_held["held_until"] is not None

    normal = await _visit_with_key(client, holder_headers, place, _phone(), "key-normal", with_plot=False)
    retry_normal = await _visit_with_key(client, holder_headers, place, _phone(), "key-normal", with_plot=False)
    assert normal["waitlisted"] is False and normal["lead_held"] is False
    assert retry_normal["id"] == normal["id"]
    assert retry_normal["waitlisted"] is False and retry_normal["lead_held"] is False
