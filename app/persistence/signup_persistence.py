"""All SQL for `employee_signups` — pending, unverified self-serve signups.

Send throttling (resend cooldown + hourly quota) is checked and recorded in the
same statement that stores the new code, so concurrent requests can't both
slip past the limit.
"""

from datetime import datetime, timedelta
from typing import Any

from app.persistence.db_persistence import Database

_SEND_WINDOW = timedelta(hours=1)

_RETURNING = """
    RETURNING id, email, employee_code, name, otp_expires_at, otp_failed_attempts,
              last_otp_sent_at, otp_sends_in_window, expires_at
"""


class SignupPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_and_reserve_send(
        self,
        email: str,
        employee_code: str,
        name: str,
        password_hash: str,
        otp_hash: str,
        otp_expires_at: datetime,
        resend_cooldown: timedelta,
        max_sends_per_window: int,
        pending_ttl: timedelta,
    ) -> dict[str, Any] | None:
        """Starts (or restarts) a pending signup with a fresh code and records the
        send. Returns None — and changes nothing — if an existing, unexpired
        signup for this email is still inside the resend cooldown or has used up
        its hourly send quota."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO employee_signups AS s (
                    email, employee_code, name, password_hash, otp_hash, otp_expires_at,
                    otp_failed_attempts, last_otp_sent_at, otp_send_window_started_at,
                    otp_sends_in_window, expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, 0, now(), now(), 1, now() + $9::interval)
                ON CONFLICT (email) DO UPDATE SET
                    employee_code = EXCLUDED.employee_code,
                    name = EXCLUDED.name,
                    password_hash = EXCLUDED.password_hash,
                    otp_hash = EXCLUDED.otp_hash,
                    otp_expires_at = EXCLUDED.otp_expires_at,
                    otp_failed_attempts = 0,
                    last_otp_sent_at = now(),
                    otp_send_window_started_at = CASE
                        WHEN s.expires_at <= now()
                          OR s.otp_send_window_started_at IS NULL
                          OR s.otp_send_window_started_at <= now() - $10::interval
                        THEN now() ELSE s.otp_send_window_started_at END,
                    otp_sends_in_window = CASE
                        WHEN s.expires_at <= now()
                          OR s.otp_send_window_started_at IS NULL
                          OR s.otp_send_window_started_at <= now() - $10::interval
                        THEN 1 ELSE s.otp_sends_in_window + 1 END,
                    expires_at = EXCLUDED.expires_at
                WHERE s.expires_at <= now()
                   OR (
                        (s.last_otp_sent_at IS NULL OR s.last_otp_sent_at <= now() - $7::interval)
                        AND (
                            s.otp_send_window_started_at IS NULL
                            OR s.otp_send_window_started_at <= now() - $10::interval
                            OR s.otp_sends_in_window < $8
                        )
                   )
                {_RETURNING}
                """,
                email,
                employee_code,
                name,
                password_hash,
                otp_hash,
                otp_expires_at,
                resend_cooldown,
                max_sends_per_window,
                pending_ttl,
                _SEND_WINDOW,
            )
        return dict(row) if row else None

    async def reserve_resend(
        self,
        email: str,
        otp_hash: str,
        otp_expires_at: datetime,
        resend_cooldown: timedelta,
        max_sends_per_window: int,
    ) -> dict[str, Any] | None:
        """Replaces the code on an unexpired pending signup and records the send.
        Returns None if there's no such signup, or it's throttled — use
        `get_active` to tell the two apart."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE employee_signups AS s SET
                    otp_hash = $2,
                    otp_expires_at = $3,
                    otp_failed_attempts = 0,
                    last_otp_sent_at = now(),
                    otp_send_window_started_at = CASE
                        WHEN s.otp_send_window_started_at IS NULL
                          OR s.otp_send_window_started_at <= now() - $6::interval
                        THEN now() ELSE s.otp_send_window_started_at END,
                    otp_sends_in_window = CASE
                        WHEN s.otp_send_window_started_at IS NULL
                          OR s.otp_send_window_started_at <= now() - $6::interval
                        THEN 1 ELSE s.otp_sends_in_window + 1 END
                WHERE s.email = $1
                  AND s.expires_at > now()
                  AND (s.last_otp_sent_at IS NULL OR s.last_otp_sent_at <= now() - $4::interval)
                  AND (
                        s.otp_send_window_started_at IS NULL
                        OR s.otp_send_window_started_at <= now() - $6::interval
                        OR s.otp_sends_in_window < $5
                  )
                {_RETURNING}
                """,
                email,
                otp_hash,
                otp_expires_at,
                resend_cooldown,
                max_sends_per_window,
                _SEND_WINDOW,
            )
        return dict(row) if row else None

    async def release_send_reservation(self, email: str, otp_hash: str) -> None:
        """Undoes a recorded send whose email failed to go out: the code is
        voided and the cooldown cleared so the user can retry immediately.
        Matching on `otp_hash` leaves a newer concurrent send untouched."""
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE employee_signups
                SET otp_hash = NULL,
                    otp_expires_at = NULL,
                    last_otp_sent_at = NULL,
                    otp_sends_in_window = GREATEST(otp_sends_in_window - 1, 0)
                WHERE email = $1 AND otp_hash = $2
                """,
                email,
                otp_hash,
            )

    async def get_active(self, email: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email FROM employee_signups WHERE email = $1 AND expires_at > now()", email
            )
        return dict(row) if row else None

    async def lock_active_for_verify(self, email: str, connection: Any) -> dict[str, Any] | None:
        """Row-locks the pending signup for the caller's transaction, so two
        concurrent verifications of the same signup are serialized. Code expiry
        is evaluated with the database clock."""
        row = await connection.fetchrow(
            """
            SELECT id, email, employee_code, name, password_hash, otp_hash, otp_failed_attempts,
                   (otp_expires_at IS NOT NULL AND otp_expires_at > now()) AS otp_is_live
            FROM employee_signups
            WHERE email = $1 AND expires_at > now()
            FOR UPDATE
            """,
            email,
        )
        return dict(row) if row else None

    async def record_failed_attempt(self, signup_id: str, max_attempts: int, connection: Any) -> int:
        """Counts a wrong code; the code is voided once `max_attempts` is reached.
        Returns the new attempt count."""
        return await connection.fetchval(
            """
            UPDATE employee_signups
            SET otp_failed_attempts = otp_failed_attempts + 1,
                otp_hash = CASE WHEN otp_failed_attempts + 1 >= $2 THEN NULL ELSE otp_hash END
            WHERE id = $1
            RETURNING otp_failed_attempts
            """,
            signup_id,
            max_attempts,
        )

    async def delete(self, signup_id: str, connection: Any) -> None:
        await connection.execute("DELETE FROM employee_signups WHERE id = $1", signup_id)

    async def delete_expired(self) -> int:
        async with self._db.acquire() as conn:
            result = await conn.execute("DELETE FROM employee_signups WHERE expires_at <= now()")
        parts = result.split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0
