"""Functional test for the 15 req/60s rate limiter (requirement: rate limiting = 15 requests)."""

from tests.conftest import auth_headers


async def test_sixteenth_request_on_same_route_is_rate_limited(client, make_employee, settings):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    statuses = []
    for _ in range(settings.rate_limit_max_requests + 1):
        response = await client.get("/api/v1/notifications", headers=headers)
        statuses.append(response.status_code)

    assert statuses[: settings.rate_limit_max_requests] == [200] * settings.rate_limit_max_requests
    assert statuses[-1] == 429


async def test_rate_limit_response_body_has_stable_error_code(client, make_employee, settings):
    employee = await make_employee()
    headers = await auth_headers(client, employee["email"], employee["password"])

    for _ in range(settings.rate_limit_max_requests):
        await client.get("/api/v1/notifications", headers=headers)

    response = await client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


async def test_random_invalid_tokens_share_the_ip_bucket(client, settings):
    # A fresh garbage token per request must not mint a fresh rate-limit bucket.
    statuses = []
    for index in range(settings.rate_limit_max_requests + 1):
        response = await client.get("/api/v1/notifications", headers={"Authorization": f"Bearer garbage-{index}"})
        statuses.append(response.status_code)

    assert statuses[-1] == 429


async def test_rate_limit_is_scoped_per_caller_not_global(client, make_employee, settings):
    employee_1 = await make_employee()
    employee_2 = await make_employee()
    headers_1 = await auth_headers(client, employee_1["email"], employee_1["password"])
    headers_2 = await auth_headers(client, employee_2["email"], employee_2["password"])

    for _ in range(settings.rate_limit_max_requests):
        response = await client.get("/api/v1/notifications", headers=headers_1)
        assert response.status_code == 200

    # employee_1 is now exhausted; employee_2 must be unaffected.
    response = await client.get("/api/v1/notifications", headers=headers_2)
    assert response.status_code == 200
