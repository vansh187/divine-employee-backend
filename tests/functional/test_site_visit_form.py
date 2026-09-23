"""Functional tests for the "Log a Site Visit" form: every field on the form is
stored on the visit exactly as entered, including for a returning customer."""

from datetime import date, timedelta

from tests.conftest import auth_headers


def _form(place: dict, **overrides) -> dict:
    today = date.today().isoformat()
    return {
        "visitor_name": "Priya Mehta",
        "phone": "+91 98555 00001",
        "email": "priya@example.com",
        "project_id": place["project_id"],
        "property_id": place["property_id"],
        "visit_at": f"{today}T10:30:00+05:30",
        "outcome": "INTERESTED",
        "notes": "Liked the corner plot; wants pricing.",
        **overrides,
    }


async def test_every_form_field_is_stored_on_the_visit(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    created = await client.post("/api/v1/site-visits", headers=headers, json=_form(place))
    assert created.status_code == 200, created.text
    visit_id = created.json()["data"]["id"]

    stored = (await client.get(f"/api/v1/site-visits/{visit_id}", headers=headers)).json()["data"]
    assert stored["employee_id"] == str(employee["id"])
    assert stored["visitor_name"] == "Priya Mehta"
    assert stored["visitor_phone"] == "+91 98555 00001"
    assert stored["visitor_email"] == "priya@example.com"
    assert stored["project_id"] == place["project_id"]
    assert stored["property_id"] == place["property_id"]
    assert stored["visit_at"].startswith(f"{date.today().isoformat()}T05:00:00")  # 10:30 IST in UTC
    assert stored["outcome"] == "INTERESTED"
    assert stored["notes"] == "Liked the corner plot; wants pricing."
    assert stored["project_name"] is not None
    assert stored["plot_no"] is not None


async def test_returning_customer_visit_keeps_its_own_details(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    first_place = await make_project_and_plot()
    second_place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    first = await client.post(
        "/api/v1/site-visits", headers=headers, json=_form(first_place, phone="9855500002", email=None)
    )
    assert first.status_code == 200, first.text

    # Same customer (same number, different format), different spelling + new email.
    second = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json=_form(
            second_place,
            visitor_name="Priya M.",
            phone="+91-98555-00002",
            email="priya.m@example.com",
            visit_at=f"{date.today().isoformat()}T16:00:00+05:30",
        ),
    )
    assert second.status_code == 200, second.text

    first_data, second_data = first.json()["data"], second.json()["data"]
    assert first_data["lead_id"] == second_data["lead_id"]
    assert first_data["visitor_name"] == "Priya Mehta"
    assert second_data["visitor_name"] == "Priya M."
    assert second_data["visitor_phone"] == "+91-98555-00002"
    assert second_data["visitor_email"] == "priya.m@example.com"

    # The Lead had no email yet, so it picks up the one from the second visit.
    lead = (await client.get(f"/api/v1/leads/{second_data['lead_id']}", headers=headers)).json()["data"]
    assert lead["email"] == "priya.m@example.com"


async def test_blank_optional_fields_from_the_form_are_accepted(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])

    # Untouched optional inputs/selects ("No specific plot", "Select outcome") arrive as "".
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json=_form(place, phone="9855500003", email="", property_id="", outcome="", notes=""),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["property_id"] is None
    assert data["visitor_email"] is None
    assert data["outcome"] is None
    assert data["notes"] is None


async def test_visit_time_without_offset_is_taken_as_ist(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json=_form(place, phone="9855500004", visit_at=f"{yesterday}T23:41"),
    )
    assert response.status_code == 200, response.text
    # 23:41 IST == 18:11 UTC on the same date (not 23:41 UTC).
    assert response.json()["data"]["visit_at"].startswith(f"{yesterday}T18:11:00")


async def test_invalid_email_returns_422(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits", headers=headers, json=_form(place, phone="9855500005", email="not-an-email")
    )
    assert response.status_code == 422
