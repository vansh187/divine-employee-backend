"""Unit tests for password hashing and JWT issuing/verification."""

import time

import pytest

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import PasswordHasher, TokenService


@pytest.fixture
def password_hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.fixture
def token_service() -> TokenService:
    return TokenService(get_settings())


def test_password_hash_and_verify_round_trip(password_hasher: PasswordHasher) -> None:
    hashed = password_hasher.hash("Sup3rSecret!")
    assert hashed != "Sup3rSecret!"
    assert password_hasher.verify("Sup3rSecret!", hashed) is True


def test_password_verify_rejects_wrong_password(password_hasher: PasswordHasher) -> None:
    hashed = password_hasher.hash("Sup3rSecret!")
    assert password_hasher.verify("wrong-password", hashed) is False


def test_password_verify_rejects_malformed_hash(password_hasher: PasswordHasher) -> None:
    assert password_hasher.verify("anything", "not-a-real-hash") is False


def test_access_token_round_trip(token_service: TokenService) -> None:
    token = token_service.create_access_token("employee-1", "E-101", "e101@example.com")
    payload = token_service.decode_token(token, expected_type="access")
    assert payload["sub"] == "employee-1"
    assert payload["employee_code"] == "E-101"
    assert payload["email"] == "e101@example.com"


def test_refresh_token_round_trip(token_service: TokenService) -> None:
    token, expires_at = token_service.create_refresh_token("employee-1")
    payload = token_service.decode_token(token, expected_type="refresh")
    assert payload["sub"] == "employee-1"
    assert expires_at.tzinfo is not None


def test_decode_rejects_wrong_token_type(token_service: TokenService) -> None:
    access_token = token_service.create_access_token("employee-1", "E-101", "e101@example.com")
    with pytest.raises(UnauthorizedError):
        token_service.decode_token(access_token, expected_type="refresh")


def test_decode_rejects_tampered_token(token_service: TokenService) -> None:
    token = token_service.create_access_token("employee-1", "E-101", "e101@example.com")
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(UnauthorizedError):
        token_service.decode_token(tampered, expected_type="access")


def test_decode_rejects_garbage_string(token_service: TokenService) -> None:
    with pytest.raises(UnauthorizedError):
        token_service.decode_token("not.a.jwt", expected_type="access")


def test_hash_token_is_deterministic_and_distinct(token_service: TokenService) -> None:
    assert token_service.hash_token("token-a") == token_service.hash_token("token-a")
    assert token_service.hash_token("token-a") != token_service.hash_token("token-b")
