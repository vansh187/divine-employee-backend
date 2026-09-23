"""JWT issuing/verification and password hashing.

Kept dependency-free of the database: token validation is purely cryptographic
(self-contained claims) so authenticated requests stay low-latency and never
need a DB round trip just to prove who the caller is.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import Settings
from app.core.exceptions import UnauthorizedError

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


class PasswordHasher:
    """Instance-based wrapper around passlib's bcrypt context."""

    def __init__(self) -> None:
        self._context = _pwd_context

    def hash(self, plain_password: str) -> str:
        return self._context.hash(plain_password)

    def verify(self, plain_password: str, password_hash: str) -> bool:
        try:
            return self._context.verify(plain_password, password_hash)
        except ValueError:
            return False


class TokenService:
    """Issues and decodes JWT access/refresh tokens for a given settings instance."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def access_token_expire_minutes(self) -> int:
        return self._settings.jwt_access_token_expire_minutes

    def create_access_token(self, employee_id: str, employee_code: str, email: str) -> str:
        expires_delta = timedelta(minutes=self._settings.jwt_access_token_expire_minutes)
        return self._encode(
            subject=employee_id,
            token_type=TOKEN_TYPE_ACCESS,
            expires_delta=expires_delta,
            extra_claims={"employee_code": employee_code, "email": email},
        )

    def create_refresh_token(self, employee_id: str) -> tuple[str, datetime]:
        expires_delta = timedelta(days=self._settings.jwt_refresh_token_expire_days)
        expires_at = datetime.now(timezone.utc) + expires_delta
        token = self._encode(subject=employee_id, token_type=TOKEN_TYPE_REFRESH, expires_delta=expires_delta)
        return token, expires_at

    def decode_token(self, token: str, expected_type: str) -> dict[str, Any]:
        try:
            payload = jwt.decode(token, self._settings.jwt_secret_key, algorithms=[self._settings.jwt_algorithm])
        except JWTError as exc:
            raise UnauthorizedError("Invalid or expired token") from exc

        if payload.get("token_type") != expected_type:
            raise UnauthorizedError("Unexpected token type")

        return payload

    def hash_token(self, token: str) -> str:
        """Deterministic digest used to look up refresh tokens in storage without keeping raw tokens."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _encode(
        self,
        subject: str,
        token_type: str,
        expires_delta: timedelta,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sub": subject,
            "token_type": token_type,
            "iat": now,
            "exp": now + expires_delta,
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self._settings.jwt_secret_key, algorithm=self._settings.jwt_algorithm)
