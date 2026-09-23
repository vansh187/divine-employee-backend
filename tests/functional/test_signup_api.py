"""Functional tests for self-serve employee signup with email OTP verification.

Email delivery is replaced by an in-memory sender (FastAPI dependency override)
that captures the codes, so these tests never send real email.
"""

import re
import uuid
from collections.abc import AsyncIterator

import pytest_asyncio

from app.core.config import get_settings
from app.core.deps import get_email_sender
from app.core.exceptions import EmailDeliveryError
from app.main import app

_DOMAIN = "divinevisioninfra.com"


class CapturingEmailSender:
    """Records every email instead of sending it; can be switched to fail."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.fail = False

    async def send(self, to_address: str, subject: str, text_body: str, html_body: str) -> None:
        if self.fail:
            raise EmailDeliveryError()
        self.sent.append({"to": to_address, "subject": subject, "text": text_body, "html": html_body})

    def last_code_for(self, email: str) -> str:
        for message in reversed(self.sent):
            if message["to"] == email:
                match = re.search(r"\b(\d{6})\b", message["text"])
                assert match, message["text"]
                return match.group(1)
        raise AssertionError(f"No email sent to {email}")


@pytest_asyncio.fixture
async def mailbox(client) -> AsyncIterator[CapturingEmailSender]:
    sender = CapturingEmailSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    try:
        yield sender
    finally:
        app.dependency_overrides.pop(get_email_sender, None)
        app.dependency_overrides.pop(get_settings, None)


def _use_settings(**overrides) -> None:
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update=overrides)


def _new_identity() -> tuple[str, str]:
    unique = uuid.uuid4().hex[:8]
    return f"new.hire.{unique}@{_DOMAIN}", f"DVI-{unique.upper()}"


def _signup_body(email: str, employee_id: str, password: str = "Str0ng-pass!") -> dict:
    return {"name": "Priya Mehta", "email": email, "employee_id": employee_id, "password": password}


async def _start(client, email: str, employee_id: str, **extra):
    return await client.post("/api/v1/auth/signup", json=_signup_body(email, employee_id, **extra))


async def _verify(client, email: str, otp: str):
    return await client.post("/api/v1/auth/signup/verify-otp", json={"email": email, "otp": otp})


async def _resend(client, email: str):
    return await client.post("/api/v1/auth/signup/resend-otp", json={"email": email})


# ---------------------------------------------------------------- happy path


async def test_full_signup_creates_active_employee_and_logs_in(client, mailbox):
    email, employee_id = _new_identity()

    started = await _start(client, email.upper(), employee_id)
    assert started.status_code == 201, started.text
    data = started.json()["data"]
    assert data["email"] == email  # normalized to lower case
    assert data["expires_at"].endswith("+05:30")
    assert set(data) == {"email", "expires_at"}  # the code itself is never returned
    code = mailbox.last_code_for(email)
    assert code not in started.text

    verified = await _verify(client, email, code)
    assert verified.status_code == 200, verified.text
    tokens = verified.json()["data"]
    assert tokens["access_token"] and tokens["refresh_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    profile = me.json()["data"]
    assert (profile["email"], profile["employee_code"], profile["status"]) == (email, employee_id, "ACTIVE")

    # Password works for normal login, with any email casing.
    login = await client.post("/api/v1/auth/login", json={"email": email.upper(), "password": "Str0ng-pass!"})
    assert login.status_code == 200, login.text

    # The pending signup is gone.
    assert (await _resend(client, email)).status_code == 404
    assert (await _verify(client, email, code)).status_code == 404


async def test_no_employee_exists_until_code_is_verified(client, mailbox, raw_conn):
    email, employee_id = _new_identity()
    assert (await _start(client, email, employee_id)).status_code == 201

    assert await raw_conn.fetchval("SELECT count(*) FROM employees WHERE lower(email) = $1", email) == 0
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ng-pass!"})
    assert login.status_code == 401

    stored = await raw_conn.fetchrow("SELECT password_hash, otp_hash FROM employee_signups WHERE email = $1", email)
    assert stored["password_hash"].startswith("$2")  # bcrypt, never plaintext
    assert mailbox.last_code_for(email) not in stored["otp_hash"]


async def test_otp_with_spaces_is_accepted(client, mailbox):
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    code = mailbox.last_code_for(email)
    assert (await _verify(client, email, f"{code[:3]} {code[3:]}")).status_code == 200


# ------------------------------------------------------------ input checks


async def test_existing_email_is_rejected_case_insensitively(client, mailbox, raw_conn):
    email, employee_id = _new_identity()
    await raw_conn.execute(
        "INSERT INTO employees (employee_code, name, email, password_hash) VALUES ($1, 'Existing', $2, 'x')",
        f"OLD-{employee_id}",
        email,
    )
    response = await _start(client, email.upper(), employee_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"
    assert mailbox.sent == []


async def test_existing_employee_id_is_rejected_case_insensitively(client, mailbox, raw_conn):
    email, employee_id = _new_identity()
    await raw_conn.execute(
        "INSERT INTO employees (employee_code, name, email, password_hash) VALUES ($1, 'Existing', $2, 'x')",
        employee_id,
        f"other.{email}",
    )
    response = await _start(client, email, employee_id.lower())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMPLOYEE_ID_ALREADY_REGISTERED"


async def test_non_company_email_is_rejected(client, mailbox):
    response = await _start(client, f"someone.{uuid.uuid4().hex[:6]}@gmail.com", "DVI-9999")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["fields"][0]["field"] == "body.email"
    assert mailbox.sent == []


async def test_individually_allowed_email_can_sign_up(client, mailbox):
    tester = f"tester.{uuid.uuid4().hex[:6]}@gmail.com"
    _use_settings(signup_allowed_emails=f"someone@else.com, {tester.upper()}")
    _, employee_id = _new_identity()

    started = await _start(client, tester, employee_id)
    assert started.status_code == 201, started.text
    assert (await _verify(client, tester, mailbox.last_code_for(tester))).status_code == 200

    # The test user can then log in normally (any casing) and use the API.
    login = await client.post("/api/v1/auth/login", json={"email": tester.upper(), "password": "Str0ng-pass!"})
    assert login.status_code == 200, login.text
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["data"]["email"] == tester

    # Other addresses on the same domain are still rejected.
    other = await _start(client, f"other.{uuid.uuid4().hex[:6]}@gmail.com", f"{employee_id}-2")
    assert other.status_code == 422


async def test_invalid_inputs_return_field_errors(client, mailbox):
    email, employee_id = _new_identity()
    cases = [
        _signup_body(email, employee_id, password="short"),
        _signup_body(email, employee_id, password="x" * 73),
        _signup_body(email, employee_id, password="        "),
        _signup_body("not-an-email", employee_id),
        _signup_body(email, "has spaces"),
        {"email": email, "employee_id": employee_id, "password": "Str0ng-pass!"},  # missing name
        {**_signup_body(email, employee_id), "name": "   "},
    ]
    for body in cases:
        response = await client.post("/api/v1/auth/signup", json=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"
        assert response.json()["error"]["fields"]
    assert mailbox.sent == []


# ---------------------------------------------------------------- wrong code


async def test_wrong_code_keeps_signup_and_counts_down(client, mailbox):
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    code = mailbox.last_code_for(email)
    wrong = "000000" if code != "000000" else "111111"

    response = await _verify(client, email, wrong)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_OTP"
    assert "4 attempts left" in response.json()["error"]["message"]

    for bad in ("12345", "abcdef"):  # malformed codes are simply incorrect
        assert (await _verify(client, email, bad)).json()["error"]["code"] == "INVALID_OTP"

    assert (await _verify(client, email, code)).status_code == 200


async def test_too_many_wrong_codes_voids_the_code_until_resend(client, mailbox):
    _use_settings(signup_otp_resend_cooldown_seconds=0)
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    code = mailbox.last_code_for(email)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(5):
        assert (await _verify(client, email, wrong)).status_code == 422

    # Even the right code no longer works — brute force is capped at 5 guesses.
    blocked = await _verify(client, email, code)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    assert (await _resend(client, email)).status_code == 200
    assert (await _verify(client, email, mailbox.last_code_for(email))).status_code == 200


async def test_expired_code_returns_410_and_resend_recovers(client, mailbox, raw_conn):
    _use_settings(signup_otp_resend_cooldown_seconds=0)
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    code = mailbox.last_code_for(email)
    await raw_conn.execute(
        "UPDATE employee_signups SET otp_expires_at = now() - interval '1 second' WHERE email = $1", email
    )

    expired = await _verify(client, email, code)
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "OTP_EXPIRED"

    resent = await _resend(client, email)
    assert resent.status_code == 200
    new_code = mailbox.last_code_for(email)
    assert (await _verify(client, email, new_code)).status_code == 200


async def test_expired_pending_signup_is_not_found(client, mailbox, raw_conn):
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    code = mailbox.last_code_for(email)
    await raw_conn.execute(
        "UPDATE employee_signups SET expires_at = now() - interval '1 second' WHERE email = $1", email
    )
    assert (await _verify(client, email, code)).status_code == 404
    assert (await _resend(client, email)).status_code == 404


# ------------------------------------------------------------------- resend


async def test_resend_replaces_the_code(client, mailbox):
    _use_settings(signup_otp_resend_cooldown_seconds=0)
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    first_code = mailbox.last_code_for(email)

    resent = await _resend(client, email)
    assert resent.status_code == 200
    assert resent.json()["data"]["email"] == email
    second_code = mailbox.last_code_for(email)

    if first_code != second_code:  # 1-in-a-million chance they collide
        assert (await _verify(client, email, first_code)).json()["error"]["code"] == "INVALID_OTP"
    assert (await _verify(client, email, second_code)).status_code == 200


async def test_resend_cooldown_is_enforced_server_side(client, mailbox):
    email, employee_id = _new_identity()  # default 30 s cooldown
    await _start(client, email, employee_id)
    response = await _resend(client, email)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert len(mailbox.sent) == 1


async def test_hourly_send_quota_is_enforced(client, mailbox):
    _use_settings(signup_otp_resend_cooldown_seconds=0, signup_otp_max_sends_per_hour=3)
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    assert (await _resend(client, email)).status_code == 200
    assert (await _resend(client, email)).status_code == 200
    assert (await _resend(client, email)).status_code == 429
    # Restarting the form doesn't bypass the quota either.
    assert (await _start(client, email, employee_id)).status_code == 429
    assert len(mailbox.sent) == 3


async def test_resend_and_verify_for_unknown_email_return_404(client, mailbox):
    email, _ = _new_identity()
    resend = await _resend(client, email)
    assert resend.status_code == 404
    assert resend.json()["error"]["code"] == "NOT_FOUND"
    assert (await _verify(client, email, "123456")).status_code == 404


# -------------------------------------------------------------- start over


async def test_resubmitting_the_form_starts_over(client, mailbox):
    _use_settings(signup_otp_resend_cooldown_seconds=0)
    email, employee_id = _new_identity()
    await _start(client, email, employee_id, password="First-pass-1")
    first_code = mailbox.last_code_for(email)

    again = await _start(client, email, employee_id, password="Second-pass-2")
    assert again.status_code == 201
    second_code = mailbox.last_code_for(email)

    if first_code != second_code:
        assert (await _verify(client, email, first_code)).json()["error"]["code"] == "INVALID_OTP"
    assert (await _verify(client, email, second_code)).status_code == 200

    # The latest submission's password is the one that works.
    assert (await client.post("/api/v1/auth/login", json={"email": email, "password": "Second-pass-2"})).status_code == 200
    assert (await client.post("/api/v1/auth/login", json={"email": email, "password": "First-pass-1"})).status_code == 401


async def test_employee_id_taken_between_signup_and_verify_is_a_conflict(client, mailbox):
    first_email, employee_id = _new_identity()
    second_email, _ = _new_identity()
    await _start(client, first_email, employee_id)
    await _start(client, second_email, employee_id)  # both pending with the same ID

    assert (await _verify(client, first_email, mailbox.last_code_for(first_email))).status_code == 200
    late = await _verify(client, second_email, mailbox.last_code_for(second_email))
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "EMPLOYEE_ID_ALREADY_REGISTERED"


# ---------------------------------------------------------- email failures


async def test_email_failure_returns_503_and_retry_is_allowed_immediately(client, mailbox, raw_conn):
    email, employee_id = _new_identity()  # default 30 s cooldown
    mailbox.fail = True
    failed = await _start(client, email, employee_id)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "EMAIL_DELIVERY_FAILED"
    # No usable code was left behind.
    assert await raw_conn.fetchval("SELECT otp_hash FROM employee_signups WHERE email = $1", email) is None

    mailbox.fail = False
    retry = await _start(client, email, employee_id)  # not blocked by the cooldown
    assert retry.status_code == 201, retry.text
    assert (await _verify(client, email, mailbox.last_code_for(email))).status_code == 200


async def test_verify_after_failed_send_reports_expired_code(client, mailbox):
    email, employee_id = _new_identity()
    await _start(client, email, employee_id)
    code = mailbox.last_code_for(email)
    _use_settings(signup_otp_resend_cooldown_seconds=0)
    mailbox.fail = True
    assert (await _resend(client, email)).status_code == 503
    # The resend voided the old code and the new one never arrived.
    assert (await _verify(client, email, code)).json()["error"]["code"] == "OTP_EXPIRED"
