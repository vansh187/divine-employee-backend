"""All SQL for `password_reset_requests` — forgot-password OTPs and reset tokens.

One row per lower-cased email. Emails with no ACTIVE employee get a row too
(employee_id NULL, no code) so throttling and wrong-code handling behave the
same whether or not the account exists. Send throttling is checked and
recorded in the same statement that stores the new code, so concurrent
requests can't both slip past the limit.
"""

from datetime import timedelta
from typing import Any

from app.persistence.db_persistence import Database

_SEND_WINDOW = timedelta(hours=1)

# Inside the upsert: the existing row's reset token is unused, unexpired and
# still belongs to the same ACTIVE employee.
_TOKEN_STILL_LIVE = """
    r.reset_token_hash IS NOT NULL
    AND r.reset_token_used_at IS NULL
    AND r.reset_token_expires_at > now()
    AND r.employee_id = EXCLUDED.employee_id
"""


class PasswordResetPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def reserve_request(
        self,
        email: str,
        otp_hash: str,
        otp_ttl: timedelta,
        resend_cooldown: timedelta,
        max_requests_per_window: int,
        request_ip: str | None,
    ) -> dict[str, Any] | None:
        """Starts a fresh reset for `email`: stores a new code (only if an ACTIVE
        employee has that email), voids any previous code (and any reset token
        that is no longer live) and records the request. Returns {employee_id, name} — both NULL for an
        unknown email — or None, changing nothing, if the email is inside the
        resend cooldown or has used up its hourly quota."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                WITH employee AS (
                    SELECT id, name FROM employees WHERE lower(email) = $1 AND status = 'ACTIVE'
                ),
                reserved AS (
                    INSERT INTO password_reset_requests AS r (
                        email, employee_id, otp_hash, otp_expires_at, otp_attempts, otp_used_at,
                        reset_token_hash, reset_token_expires_at, reset_token_used_at,
                        last_otp_sent_at, otp_send_window_started_at, otp_sends_in_window, request_ip
                    )
                    SELECT $1, employee.id, CASE WHEN employee.id IS NOT NULL THEN $2 END,
                           now() + $3::interval, 0, NULL, NULL, NULL, NULL, now(), now(), 1, $7
                    FROM (SELECT 1) AS one LEFT JOIN employee ON true
                    ON CONFLICT (email) DO UPDATE SET
                        employee_id = EXCLUDED.employee_id,
                        otp_hash = EXCLUDED.otp_hash,
                        otp_expires_at = EXCLUDED.otp_expires_at,
                        otp_attempts = 0,
                        otp_used_at = NULL,
                        -- A reset token that's already live (the owner proved access to
                        -- the inbox) survives: otherwise anyone could cancel someone
                        -- else's reset just by requesting codes for their email.
                        reset_token_hash = CASE WHEN {_TOKEN_STILL_LIVE} THEN r.reset_token_hash END,
                        reset_token_expires_at = CASE WHEN {_TOKEN_STILL_LIVE} THEN r.reset_token_expires_at END,
                        reset_token_used_at = NULL,
                        last_otp_sent_at = now(),
                        otp_send_window_started_at = CASE
                            WHEN r.otp_send_window_started_at IS NULL
                              OR r.otp_send_window_started_at <= now() - $6::interval
                            THEN now() ELSE r.otp_send_window_started_at END,
                        otp_sends_in_window = CASE
                            WHEN r.otp_send_window_started_at IS NULL
                              OR r.otp_send_window_started_at <= now() - $6::interval
                            THEN 1 ELSE r.otp_sends_in_window + 1 END,
                        request_ip = EXCLUDED.request_ip
                    WHERE (r.last_otp_sent_at IS NULL OR r.last_otp_sent_at <= now() - $4::interval)
                      AND (
                            r.otp_send_window_started_at IS NULL
                            OR r.otp_send_window_started_at <= now() - $6::interval
                            OR r.otp_sends_in_window < $5
                      )
                    RETURNING r.employee_id
                )
                SELECT reserved.employee_id, employee.name
                FROM reserved LEFT JOIN employee ON employee.id = reserved.employee_id
                """,
                email,
                otp_hash,
                otp_ttl,
                resend_cooldown,
                max_requests_per_window,
                _SEND_WINDOW,
                request_ip,
            )
        return dict(row) if row else None

    async def release_send_reservation(self, email: str, otp_hash: str) -> None:
        """Undoes a recorded request whose email failed to go out: the code is
        voided and the cooldown cleared so the user can retry immediately.
        Matching on `otp_hash` leaves a newer concurrent request untouched."""
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE password_reset_requests
                SET otp_hash = NULL,
                    otp_expires_at = NULL,
                    last_otp_sent_at = NULL,
                    otp_sends_in_window = GREATEST(otp_sends_in_window - 1, 0)
                WHERE email = $1 AND otp_hash = $2
                """,
                email,
                otp_hash,
            )

    async def lock_for_verify(self, email: str, connection: Any) -> dict[str, Any] | None:
        """Row-locks the request for the caller's transaction, so concurrent
        verifications are serialized. Expiry uses the database clock."""
        row = await connection.fetchrow(
            """
            SELECT id, employee_id, otp_hash, otp_attempts, otp_used_at,
                   (otp_expires_at IS NOT NULL AND otp_expires_at > now()) AS otp_is_live
            FROM password_reset_requests
            WHERE email = $1
            FOR UPDATE
            """,
            email,
        )
        return dict(row) if row else None

    async def record_failed_attempt(self, request_id: str, max_attempts: int, connection: Any) -> int:
        """Counts a wrong code; the code is voided once `max_attempts` is reached.
        Returns the new attempt count."""
        return await connection.fetchval(
            """
            UPDATE password_reset_requests
            SET otp_attempts = otp_attempts + 1,
                otp_hash = CASE WHEN otp_attempts + 1 >= $2 THEN NULL ELSE otp_hash END
            WHERE id = $1
            RETURNING otp_attempts
            """,
            request_id,
            max_attempts,
        )

    async def issue_reset_token(
        self, request_id: str, reset_token_hash: str, token_ttl: timedelta, connection: Any
    ) -> None:
        """Marks the code used (it can't be verified again) and stores the reset token."""
        await connection.execute(
            """
            UPDATE password_reset_requests
            SET otp_used_at = now(),
                otp_hash = NULL,
                reset_token_hash = $2,
                reset_token_expires_at = now() + $3::interval,
                reset_token_used_at = NULL
            WHERE id = $1
            """,
            request_id,
            reset_token_hash,
            token_ttl,
        )

    async def reset_token_is_live(self, reset_token_hash: str) -> bool:
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM password_reset_requests
                    WHERE reset_token_hash = $1
                      AND reset_token_used_at IS NULL
                      AND reset_token_expires_at > now()
                      AND employee_id IS NOT NULL
                )
                """,
                reset_token_hash,
            )
        return bool(value)

    async def consume_reset_token(self, reset_token_hash: str, connection: Any) -> str | None:
        """Atomically marks a live reset token used and returns its employee id.
        Two concurrent resets with the same token race on this one UPDATE, so only one wins."""
        employee_id = await connection.fetchval(
            """
            UPDATE password_reset_requests
            SET reset_token_used_at = now()
            WHERE reset_token_hash = $1
              AND reset_token_used_at IS NULL
              AND reset_token_expires_at > now()
              AND employee_id IS NOT NULL
            RETURNING employee_id
            """,
            reset_token_hash,
        )
        return str(employee_id) if employee_id else None

    async def delete_stale(self) -> int:
        """Rows untouched for a day hold no live code, token or throttle window."""
        async with self._db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM password_reset_requests WHERE updated_at <= now() - interval '1 day'"
            )
        parts = result.split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0
