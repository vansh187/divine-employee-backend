"""Functional tests for /api/v1/auth/*."""

from tests.conftest import auth_headers, login


async def test_login_success_returns_token_pair(client, make_employee):
    employee = await make_employee()
    data = await login(client, employee["email"], employee["password"])
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"


async def test_login_wrong_password_returns_401(client, make_employee):
    employee = await make_employee()
    response = await client.post(
        "/api/v1/auth/login", json={"email": employee["email"], "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_login_unknown_email_returns_401(client):
    response = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert response.status_code == 401


async def test_login_inactive_employee_is_rejected(client, make_employee):
    employee = await make_employee(status="INACTIVE")
    response = await client.post(
        "/api/v1/auth/login", json={"email": employee["email"], "password": employee["password"]}
    )
    assert response.status_code == 401


async def test_login_malformed_email_returns_422_not_500(client):
    response = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "x"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_protected_endpoint_without_token_returns_401_or_403(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code in (401, 403)


async def test_protected_endpoint_with_garbage_token_returns_401(client):
    response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage.token.value"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_returns_current_employee_profile(client, make_employee):
    employee = await make_employee(name="Profile Test")
    headers = await auth_headers(client, employee["email"], employee["password"])
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Profile Test"


async def test_refresh_token_rotation_old_token_is_revoked(client, make_employee):
    employee = await make_employee()
    tokens = await login(client, employee["email"], employee["password"])

    refresh_response = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 200
    new_tokens = refresh_response.json()["data"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    replay_response = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay_response.status_code == 401
    assert replay_response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_logout_revokes_refresh_token(client, make_employee):
    employee = await make_employee()
    tokens = await login(client, employee["email"], employee["password"])

    logout_response = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout_response.status_code == 200

    reuse_response = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse_response.status_code == 401
