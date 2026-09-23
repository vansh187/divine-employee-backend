"""Functional tests proving the global safety net: no unhandled exception ever
crashes a request or leaks internals (stack traces, SQL, file paths) to the client.
"""

from tests.conftest import auth_headers


async def test_malformed_json_body_returns_422_not_500(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers={**headers, "Content-Type": "application/json"},
        content=b"{not valid json",
    )
    assert response.status_code == 422


async def test_unknown_route_returns_clean_404(client):
    response = await client.get("/api/v1/this-route-does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert "Traceback" not in str(body)
    assert "asyncpg" not in str(body).lower()


async def test_malformed_uuid_path_param_returns_422_not_500(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/site-visits/not-a-valid-uuid", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_malformed_uuid_in_lead_path_returns_422(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/leads/definitely-not-a-uuid", headers=headers)
    assert response.status_code == 422


async def test_oversized_page_size_is_rejected_not_500(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/site-visits", headers=headers, params={"page_size": 999999})
    assert response.status_code == 422


async def test_negative_page_is_rejected_not_500(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/site-visits", headers=headers, params={"page": -1})
    assert response.status_code == 422


async def test_extremely_long_string_field_does_not_crash(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "A" * 50_000,
            "phone": "9811100099",
            "project_id": place["project_id"],
            "visit_at": "2026-09-25T11:00:00+05:30",
            "notes": "B" * 200_000,
        },
    )
    assert response.status_code < 500


async def test_sql_injection_style_input_is_treated_as_literal_data(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "Robert'); DROP TABLE employees;--",
            "phone": "9811100098",
            "project_id": place["project_id"],
            "visit_at": "2026-09-25T11:00:00+05:30",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["lead_name"] == "Robert'); DROP TABLE employees;--"

    # Prove the table really does still exist.
    verify = await client.get("/api/v1/auth/me", headers=headers)
    assert verify.status_code == 200


async def test_unicode_and_emoji_input_does_not_crash(client, make_employee, make_project_and_plot):
    employee = await make_employee()
    place = await make_project_and_plot()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/site-visits",
        headers=headers,
        json={
            "visitor_name": "राहुल शर्मा 🏠🎉",
            "phone": "9811100097",
            "project_id": place["project_id"],
            "visit_at": "2026-09-25T11:00:00+05:30",
            "notes": "Interested 😊",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["lead_name"] == "राहुल शर्मा 🏠🎉"


async def test_wrong_content_type_does_not_crash(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post(
        "/api/v1/day-off/select",
        headers={**headers, "Content-Type": "text/plain"},
        content=b"day_off_date=2026-09-25",
    )
    assert response.status_code < 500


async def test_empty_body_on_required_post_returns_422(client, make_employee):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.post("/api/v1/site-visits", headers=headers, content=b"")
    assert response.status_code == 422


async def test_expired_style_garbage_bearer_token_never_500s(client):
    response = await client.get(
        "/api/v1/dashboard", headers={"Authorization": "Bearer " + "a" * 2000}
    )
    assert response.status_code == 401


async def test_missing_bearer_scheme_returns_401_or_403_not_500(client):
    employee_header_variants = ["NotBearer abc.def.ghi", "Bearer", ""]
    for value in employee_header_variants:
        headers = {"Authorization": value} if value else {}
        response = await client.get("/api/v1/dashboard", headers=headers)
        assert response.status_code in (401, 403)
