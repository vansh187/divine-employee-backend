"""All SQL for the `employees` and `refresh_tokens` tables lives here — nowhere else."""

from datetime import datetime
from typing import Any

from app.persistence.db_persistence import Database


class EmployeePersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, employee_code, name, email, phone, password_hash, team, designation,
                       status, weekly_day_off_allowance, business_timezone, created_at, updated_at
                FROM employees
                WHERE email = $1
                """,
                email,
            )
        return dict(row) if row else None

    async def get_by_id(self, employee_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, employee_code, name, email, phone, team, designation,
                       status, weekly_day_off_allowance, business_timezone, created_at, updated_at
                FROM employees
                WHERE id = $1
                """,
                employee_id,
            )
        return dict(row) if row else None

    async def store_refresh_token(
        self, employee_id: str, token_hash: str, expires_at: datetime
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO refresh_tokens (employee_id, token_hash, expires_at)
                VALUES ($1, $2, $3)
                """,
                employee_id,
                token_hash,
                expires_at,
            )

    async def consume_refresh_token(self, token_hash: str) -> dict[str, Any] | None:
        """Atomically revokes an active refresh token and returns it. Two concurrent
        refreshes with the same token race on this single UPDATE, so only one wins."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE refresh_tokens
                SET revoked_at = now()
                WHERE token_hash = $1 AND revoked_at IS NULL AND expires_at > now()
                RETURNING id, employee_id, token_hash, expires_at, revoked_at
                """,
                token_hash,
            )
        return dict(row) if row else None

    async def set_replaced_by(self, token_hash: str, replaced_by_token_hash: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE refresh_tokens SET replaced_by_token_hash = $2 WHERE token_hash = $1",
                token_hash,
                replaced_by_token_hash,
            )

    async def revoke_refresh_token(self, token_hash: str, replaced_by_token_hash: str | None = None) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE refresh_tokens
                SET revoked_at = now(), replaced_by_token_hash = $2
                WHERE token_hash = $1
                """,
                token_hash,
                replaced_by_token_hash,
            )

    async def revoke_all_refresh_tokens(self, employee_id: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE refresh_tokens
                SET revoked_at = now()
                WHERE employee_id = $1 AND revoked_at IS NULL
                """,
                employee_id,
            )
