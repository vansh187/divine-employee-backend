"""Unit tests for the CORS allow-list. Preflight requests are answered by the
CORS middleware before routing, so no database is needed."""

import httpx
import pytest

from app.core.config import get_settings
from app.main import app

_ALLOWED = [
    "https://employee.divinevisioninfra.com",
    "http://employee.divinevisioninfra.com",
    "https://www.employee.divinevisioninfra.com",
    "http://www.employee.divinevisioninfra.com",
]


async def _preflight(origin: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )


@pytest.mark.parametrize("origin", _ALLOWED)
async def test_frontend_origins_are_allowed(origin: str) -> None:
    response = await _preflight(origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin", ["http://localhost:5175", "https://evil.example.com", "https://divinevisioninfra.com.evil.com"]
)
async def test_other_origins_are_rejected(origin: str) -> None:
    response = await _preflight(origin)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_origin_list_parsing_strips_whitespace_and_trailing_slash() -> None:
    settings = get_settings().model_copy(update={"cors_allowed_origins": " https://a.com/ , ,http://b.com"})
    assert settings.cors_allowed_origin_list == ["https://a.com", "http://b.com"]
