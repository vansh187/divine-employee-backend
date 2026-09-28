"""Functional tests for forgot password: request code → verify code → reset password.

Email delivery is replaced by the in-memory sender from the signup tests, so
these tests never send real email.
"""

import uuid

from tests.conftest import login
from tests.functional.test_signup_api import _use_settings, mailbox  # noqa: F401  (fixture)

_BASE = "/api/v1/auth"
_NEW_PASSWORD = "NewSecret123"


async def _request(client, email: str):
    return await client.post(f"{_BASE}/forgot-password", json={"email": email})


async def _verify(client, email: str, otp: str):
    return await client.post(f"{_BASE}/forgot-password/verify-otp", json={"email": email, "otp": otp})


async def _reset(client, reset_token: str, new_password: str = _NEW_PASSWORD):
    return await client.post(f"{_BASE}/reset-password", json={"reset_token": reset_token, "new_password": new_password})


async def _reset_token_for(client, mailbox, email: str) -> str:
    assert (await _request(client, email)).status_code == 200
    verified = await _verify(client, email, mailbox.last_code_for(email))
    assert verified.status_code == 200, verified.text
    return verified.json()["data"]["reset_token"]


def _unknown_email() -> str:
    return f"nobody-{uuid.uuid4().hex[:8]}@divinevisioninfra.com"


def _error(response) -> tuple[int, str, str]:
    error = response.json()["error"]
    return response.status_code, error["code"], error["message"]


# ---------------------------------------------------------------- happy path


async def test_full_reset_changes_password_and_revokes_sessions(client, mailbox, make_employee, raw_conn):
    employee = await make_employee(name="Rohan Kaushik")
    old_session = await login(client, employee["email"], employee["password"])

    requested = await _request(client, f"  {employee['email'].upper()}  ")
    assert requested.status_code == 200, requested.text
    assert requested.json()["data"] == {"email": employee["email"]}

    message = mailbox.sent[-1]
    assert message["to"] == employee["email"]
    assert message["subject"] == "Your Divine Vision password reset code"
    assert "10 minutes" in message["text"]
    assert "If you didn't request this, you can ignore this email." in message["text"]
    code = mailbox.last_code_for(employee["email"])
    assert code not in requested.text

    verified = await _verify(client, employee["email"], code)
    assert verified.status_code == 200, verified.text
    reset_token = verified.json()["data"]["reset_token"]
    assert set(verified.json()["data"]) == {"reset_token"}
    assert len(reset_token) >= 32

    reset = await _reset(client, reset_token)
    assert reset.status_code == 200, reset.text
    assert reset.json()["data"] == {"ok": True}

    old_login = await client.post(f"{_BASE}/login", json={"email": employee["email"], "password": employee["password"]})
    assert old_login.status_code == 401
    await login(client, employee["email"], _NEW_PASSWORD)

    refreshed = await client.post(f"{_BASE}/refresh", json={"refresh_token": old_session["refresh_token"]})
    assert refreshed.status_code == 401

    assert mailbox.sent[-1]["subject"] == "Your Divine Vision password was changed"

    stored = await raw_conn.fetchrow(
        "SELECT otp_hash, reset_token_hash FROM password_reset_requests WHERE email = $1", employee["email"]
    )
    assert stored["otp_hash"] is None  # used codes are cleared
    assert reset_token not in (stored["reset_token_hash"] or "")  # only the hash is kept


async def test_reset_token_is_single_use(client, mailbox, make_employee):
    employee = await make_employee()
    reset_token = await _reset_token_for(client, mailbox, employee["email"])
    assert (await _reset(client, reset_token)).status_code == 200

    status, code, message = _error(await _reset(client, reset_token, "AnotherPass123"))
    assert (status, code, message) == (410, "RESET_EXPIRED", "This reset session has expired. Start again.")


async def test_otp_cannot_be_verified_twice(client, mailbox, make_employee):
    employee = await make_employee()
    assert (await _request(client, employee["email"])).status_code == 200
    code = mailbox.last_code_for(employee["email"])
    assert (await _verify(client, employee["email"], code)).status_code == 200

    status, error_code, _ = _error(await _verify(client, employee["email"], code))
    assert (status, error_code) == (410, "OTP_EXPIRED")


# ---------------------------------------------------------- no enumeration


async def test_unknown_email_gets_same_response_and_no_email(client, mailbox):
    email = _unknown_email()
    response = await _request(client, email)
    assert response.status_code == 200
    assert response.json()["data"] == {"email": email}
    assert mailbox.sent == []

    status, code, message = _error(await _verify(client, email, "123456"))
    assert (status, code, message) == (422, "INVALID_OTP", "That code is incorrect. Please check and try again.")


async def test_inactive_employee_gets_no_email(client, mailbox, make_employee):
    employee = await make_employee(status="INACTIVE")
    assert (await _request(client, employee["email"])).status_code == 200
    assert mailbox.sent == []


async def test_unknown_email_is_throttled_like_a_known_one(client, mailbox, make_employee):
    employee = await make_employee()
    email = _unknown_email()
    for address in (employee["email"], email):
        assert (await _request(client, address)).status_code == 200
        assert (await _request(client, address)).status_code == 429  # inside the 30s cooldown


async def test_wrong_code_attempts_expire_unknown_email_like_a_known_one(client, mailbox, make_employee):
    employee = await make_employee()
    email = _unknown_email()
    for address in (employee["email"], email):
        assert (await _request(client, address)).status_code == 200
        results = [_error(await _verify(client, address, "000000"))[:2] for _ in range(5)]
        assert results == [(422, "INVALID_OTP")] * 4 + [(410, "OTP_EXPIRED")]


# -------------------------------------------------------------- OTP rules


async def test_resend_invalidates_previous_code(client, mailbox, make_employee):
    _use_settings(password_reset_resend_cooldown_seconds=0)
    employee = await make_employee()
    assert (await _request(client, employee["email"])).status_code == 200
    first_code = mailbox.last_code_for(employee["email"])

    assert (await _request(client, employee["email"])).status_code == 200
    second_code = mailbox.last_code_for(employee["email"])
    if first_code != second_code:
        assert _error(await _verify(client, employee["email"], first_code))[:2] == (422, "INVALID_OTP")
    assert (await _verify(client, employee["email"], second_code)).status_code == 200


async def test_new_request_does_not_cancel_a_live_reset_token(client, mailbox, make_employee):
    # Otherwise anyone could cancel someone else's reset by requesting codes for their email.
    _use_settings(password_reset_resend_cooldown_seconds=0)
    employee = await make_employee()
    reset_token = await _reset_token_for(client, mailbox, employee["email"])

    assert (await _request(client, employee["email"])).status_code == 200
    assert (await _reset(client, reset_token)).status_code == 200


async def test_verifying_a_new_code_replaces_the_previous_reset_token(client, mailbox, make_employee):
    _use_settings(password_reset_resend_cooldown_seconds=0)
    employee = await make_employee()
    first_token = await _reset_token_for(client, mailbox, employee["email"])
    second_token = await _reset_token_for(client, mailbox, employee["email"])

    assert _error(await _reset(client, first_token))[:2] == (410, "RESET_EXPIRED")
    assert (await _reset(client, second_token)).status_code == 200


async def test_five_wrong_codes_stop_the_real_code_working(client, mailbox, make_employee):
    employee = await make_employee()
    assert (await _request(client, employee["email"])).status_code == 200
    code = mailbox.last_code_for(employee["email"])
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    for _ in range(4):
        status, error_code, message = _error(await _verify(client, employee["email"], wrong))
        assert (status, error_code, message) == (422, "INVALID_OTP", "That code is incorrect. Please check and try again.")
    assert _error(await _verify(client, employee["email"], wrong))[:2] == (410, "OTP_EXPIRED")
    assert _error(await _verify(client, employee["email"], code))[:2] == (410, "OTP_EXPIRED")


async def test_expired_code_returns_410(client, mailbox, make_employee, raw_conn):
    employee = await make_employee()
    assert (await _request(client, employee["email"])).status_code == 200
    code = mailbox.last_code_for(employee["email"])
    await raw_conn.execute(
        "UPDATE password_reset_requests SET otp_expires_at = now() - interval '1 second' WHERE email = $1",
        employee["email"],
    )

    status, error_code, message = _error(await _verify(client, employee["email"], code))
    assert (status, error_code, message) == (410, "OTP_EXPIRED", "This code has expired. Request a new one.")


async def test_code_with_spaces_is_accepted(client, mailbox, make_employee):
    employee = await make_employee()
    assert (await _request(client, employee["email"])).status_code == 200
    code = mailbox.last_code_for(employee["email"])
    assert (await _verify(client, employee["email"], f"{code[:3]} {code[3:]}")).status_code == 200


async def test_hourly_request_quota_returns_429(client, mailbox, make_employee):
    _use_settings(password_reset_resend_cooldown_seconds=0, password_reset_max_requests_per_hour=2)
    employee = await make_employee()
    assert (await _request(client, employee["email"])).status_code == 200
    assert (await _request(client, employee["email"])).status_code == 200
    assert (await _request(client, employee["email"])).status_code == 429


async def test_email_delivery_failure_looks_like_success_and_allows_retry(client, mailbox, make_employee):
    # A 503 only for real accounts would reveal which emails are registered.
    employee = await make_employee()
    mailbox.fail = True
    response = await _request(client, employee["email"])
    assert response.status_code == 200
    assert response.json()["data"] == {"email": employee["email"]}

    mailbox.fail = False
    assert (await _request(client, employee["email"])).status_code == 200  # cooldown was released
    assert mailbox.last_code_for(employee["email"])


async def test_password_changed_notice_failure_does_not_fail_the_reset(client, mailbox, make_employee):
    employee = await make_employee()
    reset_token = await _reset_token_for(client, mailbox, employee["email"])
    mailbox.fail = True
    assert (await _reset(client, reset_token)).status_code == 200
    await login(client, employee["email"], _NEW_PASSWORD)


# --------------------------------------------------------- reset password rules


async def test_short_password_returns_422_with_field(client, mailbox, make_employee):
    employee = await make_employee()
    reset_token = await _reset_token_for(client, mailbox, employee["email"])

    response = await _reset(client, reset_token, "short")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["fields"] == [{"field": "new_password", "message": "Password must be at least 8 characters."}]

    # The token wasn't spent by the rejected attempt.
    assert (await _reset(client, reset_token)).status_code == 200


async def test_expired_reset_token_returns_410(client, mailbox, make_employee, raw_conn):
    employee = await make_employee()
    reset_token = await _reset_token_for(client, mailbox, employee["email"])
    await raw_conn.execute(
        "UPDATE password_reset_requests SET reset_token_expires_at = now() - interval '1 second' WHERE email = $1",
        employee["email"],
    )
    assert _error(await _reset(client, reset_token))[:2] == (410, "RESET_EXPIRED")


async def test_made_up_reset_token_returns_410(client):
    assert _error(await _reset(client, "not-a-real-token"))[:2] == (410, "RESET_EXPIRED")


async def test_employee_deactivated_after_verify_cannot_reset(client, mailbox, make_employee, raw_conn):
    employee = await make_employee()
    reset_token = await _reset_token_for(client, mailbox, employee["email"])
    await raw_conn.execute("UPDATE employees SET status = 'INACTIVE' WHERE id = $1", employee["id"])
    assert _error(await _reset(client, reset_token))[:2] == (410, "RESET_EXPIRED")


async def test_invalid_email_returns_422(client, mailbox):
    for body in ({"email": ""}, {"email": "not-an-email"}, {}):
        response = await client.post(f"{_BASE}/forgot-password", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_endpoints_need_no_authorization_header(client, mailbox):
    for path, body in (
        ("/forgot-password", {"email": _unknown_email()}),
        ("/forgot-password/verify-otp", {"email": _unknown_email(), "otp": "123456"}),
        ("/reset-password", {"reset_token": "x", "new_password": _NEW_PASSWORD}),
    ):
        response = await client.post(f"{_BASE}{path}", json=body)
        assert response.status_code not in (401, 403), (path, response.text)
