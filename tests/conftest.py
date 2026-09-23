"""Shared fixtures for unit + functional tests.

Functional tests run against the real Supabase Postgres instance configured in
.env (this project's own dev database). The session-scoped `_reset_test_db`
fixture truncates all application tables once at the start of the test run so
every test starts from a known-empty state; test data created afterwards is
managed per-fixture.
"""

import uuid
from decimal import Decimal

import asyncpg
import httpx
import pytest
import pytest_asyncio
from passlib.context import CryptContext

from app.core.config import get_settings
from app.main import app

_TEST_PASSWORD = "Test@1234"
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_TEST_PASSWORD_HASH = _pwd_context.hash(_TEST_PASSWORD)

_ALL_TABLES = [
    "deal_locks", "deals", "opportunity_resolutions", "opportunity_claims", "opportunities",
    "property_locks", "lead_locks", "follow_up_actions", "site_visits", "leads",
    "attendance_records", "weekly_day_offs", "notifications", "audit_events",
    "properties", "projects", "channel_partners", "refresh_tokens", "employees",
]


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def _reset_test_db(settings):
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        await conn.execute(f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def raw_conn(settings, _reset_test_db):
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def client(_reset_test_db):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac


@pytest_asyncio.fixture
async def make_employee(raw_conn):
    created_emails = []

    async def _make(name: str = "Test Employee", status: str = "ACTIVE") -> dict:
        unique = uuid.uuid4().hex[:8]
        email = f"employee-{unique}@example.com"
        employee_code = f"E-{unique}"
        row = await raw_conn.fetchrow(
            """
            INSERT INTO employees (employee_code, name, email, phone, password_hash, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, employee_code, email
            """,
            employee_code,
            name,
            email,
            "9000000000",
            _TEST_PASSWORD_HASH,
            status,
        )
        created_emails.append(email)
        return {"id": row["id"], "employee_code": row["employee_code"], "email": row["email"], "password": _TEST_PASSWORD}

    yield _make


@pytest_asyncio.fixture
async def make_project_and_plot(raw_conn):
    async def _make(plot_no: str | None = None) -> dict:
        unique = uuid.uuid4().hex[:8]
        project = await raw_conn.fetchrow(
            "INSERT INTO projects (name, location, phase) VALUES ($1, $2, $3) RETURNING id",
            f"Test Project {unique}",
            "Test City",
            "Phase 1",
        )
        plot = await raw_conn.fetchrow(
            "INSERT INTO properties (project_id, plot_no, unit_type) VALUES ($1, $2, 'Plot') RETURNING id",
            project["id"],
            plot_no or f"P-{unique}",
        )
        return {"project_id": str(project["id"]), "property_id": str(plot["id"])}

    yield _make


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def auth_headers(client: httpx.AsyncClient, email: str, password: str) -> dict:
    tokens = await login(client, email, password)
    return {"Authorization": f"Bearer {tokens['access_token']}"}
