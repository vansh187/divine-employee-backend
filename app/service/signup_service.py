"""Business logic for self-serve employee signup with email OTP verification.

Flow: `start_signup` stores a *pending* signup and emails a 6-digit code →
`verify` checks it, creates the ACTIVE employee and logs them in. No employee
row (and so no login) exists until the code is verified. `resend` replaces
the code on a pending signup.

Failure handling, per public method:
  * business-rule failures raise a specific AppError (409/410/422/429/404);
  * email delivery failures raise EmailDeliveryError (503) and void the code
    that never went out, so the user can retry immediately;
  * database connectivity failures raise ServiceUnavailableError (503);
  * anything unexpected is logged here and re-raised to the global handler,
    which answers with a sanitized 500 — never a stack trace.
"""

import asyncio
import html
import logging
from datetime import datetime, timedelta

import asyncpg

from app.core.config import Settings
from app.core.datetime_utils import BusinessClock
from app.core.email_sender import EmailSender
from app.core.exceptions import (
    AppError,
    EmailAlreadyRegisteredError,
    EmailDeliveryError,
    EmployeeIdAlreadyRegisteredError,
    InvalidOtpError,
    NotFoundError,
    OtpExpiredError,
    RateLimitExceededError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.core.otp import OtpCodec
from app.core.security import PasswordHasher
from app.persistence.db_persistence import Database
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.signup_persistence import SignupPersistence
from app.schemas.auth_schema import TokenPairResponse
from app.schemas.signup_schema import SignupPendingResponse, SignupRequest
from app.service.auth_service import AuthService

logger = logging.getLogger("divine_vision.signup_service")

# Connection drops, pool/network timeouts, statement timeouts and "too many
# connections" — the request can safely be retried, so they map to 503.
_TRANSIENT_DB_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.exceptions.TooManyConnectionsError,
    asyncpg.exceptions.QueryCanceledError,
    OSError,
    asyncio.TimeoutError,
    TimeoutError,
)

_NOT_FOUND_MESSAGE = "No pending signup for this email. Start the signup form again."
_THROTTLED_MESSAGE = "Please wait a moment before requesting another code."


class SignupService:
    def __init__(
        self,
        db: Database,
        signup_persistence: SignupPersistence,
        employee_persistence: EmployeePersistence,
        auth_service: AuthService,
        password_hasher: PasswordHasher,
        otp_codec: OtpCodec,
        email_sender: EmailSender,
        business_clock: BusinessClock,
        settings: Settings,
    ) -> None:
        self._db = db
        self._signup_persistence = signup_persistence
        self._employee_persistence = employee_persistence
        self._auth_service = auth_service
        self._password_hasher = password_hasher
        self._otp_codec = otp_codec
        self._email_sender = email_sender
        self._business_clock = business_clock
        self._settings = settings

    # ------------------------------------------------------------------ signup

    async def start_signup(self, payload: SignupRequest) -> SignupPendingResponse:
        try:
            email = self._normalize_email(payload.email)
            self._assert_allowed_domain(email)

            if await self._employee_persistence.email_exists(email):
                raise EmailAlreadyRegisteredError()
            if await self._employee_persistence.employee_code_exists(payload.employee_id):
                raise EmployeeIdAlreadyRegisteredError()

            # bcrypt is deliberately slow (~100-300 ms of CPU) — keep it off the event loop.
            password_hash = await asyncio.to_thread(self._password_hasher.hash, payload.password)
            code = self._otp_codec.generate()
            otp_hash = self._otp_codec.digest(email, code)

            reserved = await self._signup_persistence.upsert_and_reserve_send(
                email=email,
                employee_code=payload.employee_id,
                name=payload.name,
                password_hash=password_hash,
                otp_hash=otp_hash,
                otp_expires_at=self._new_otp_expiry(),
                resend_cooldown=timedelta(seconds=self._settings.signup_otp_resend_cooldown_seconds),
                max_sends_per_window=self._settings.signup_otp_max_sends_per_hour,
                pending_ttl=timedelta(hours=self._settings.signup_pending_ttl_hours),
            )
            if reserved is None:
                raise RateLimitExceededError(_THROTTLED_MESSAGE)

            await self._deliver_code(email, payload.name, code, otp_hash)
            return self._to_pending_response(reserved)
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("Signup failed on a transient database error: %s: %s", type(exc).__name__, exc)
            raise ServiceUnavailableError() from exc
        except Exception:
            logger.exception("Unexpected error starting signup")
            raise

    # ------------------------------------------------------------------ resend

    async def resend_code(self, raw_email: str) -> SignupPendingResponse:
        try:
            email = self._normalize_email(raw_email)
            code = self._otp_codec.generate()
            otp_hash = self._otp_codec.digest(email, code)

            reserved = await self._signup_persistence.reserve_resend(
                email=email,
                otp_hash=otp_hash,
                otp_expires_at=self._new_otp_expiry(),
                resend_cooldown=timedelta(seconds=self._settings.signup_otp_resend_cooldown_seconds),
                max_sends_per_window=self._settings.signup_otp_max_sends_per_hour,
            )
            if reserved is None:
                if await self._signup_persistence.get_active(email) is None:
                    raise NotFoundError(_NOT_FOUND_MESSAGE)
                raise RateLimitExceededError(_THROTTLED_MESSAGE)

            await self._deliver_code(email, reserved["name"], code, otp_hash)
            return self._to_pending_response(reserved)
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("OTP resend failed on a transient database error: %s: %s", type(exc).__name__, exc)
            raise ServiceUnavailableError() from exc
        except Exception:
            logger.exception("Unexpected error resending signup code")
            raise

    # ------------------------------------------------------------------ verify

    async def verify(self, raw_email: str, otp: str) -> TokenPairResponse:
        try:
            email = self._normalize_email(raw_email)
            employee = await self._verify_and_create_employee(email, otp)
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("OTP verification failed on a transient database error: %s: %s", type(exc).__name__, exc)
            raise ServiceUnavailableError() from exc
        except Exception:
            logger.exception("Unexpected error verifying signup code")
            raise

        # The account now exists. If issuing tokens fails, the user can still
        # log in normally — say so rather than implying signup failed.
        try:
            return await self._auth_service.issue_tokens(
                str(employee["id"]), employee["employee_code"], employee["email"]
            )
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("Tokens not issued after signup of %s: %s: %s", email, type(exc).__name__, exc)
            raise ServiceUnavailableError("Your account was created. Please log in.") from exc
        except Exception:
            logger.exception("Unexpected error issuing tokens after signup of %s", email)
            raise

    async def _verify_and_create_employee(self, email: str, otp: str) -> dict:
        failed_attempts: int | None = None
        try:
            async with self._db.transaction() as conn:
                pending = await self._signup_persistence.lock_active_for_verify(email, conn)
                if pending is None:
                    raise NotFoundError(_NOT_FOUND_MESSAGE)
                if pending["otp_hash"] is None:
                    if pending["otp_failed_attempts"] >= self._settings.signup_otp_max_attempts:
                        raise RateLimitExceededError("Too many incorrect codes. Request a new code.")
                    raise OtpExpiredError()
                if not pending["otp_is_live"]:
                    raise OtpExpiredError()

                if not self._otp_codec.matches(email, otp, pending["otp_hash"]):
                    # Must be committed, so it's recorded here and raised after
                    # the transaction closes (raising inside would roll it back).
                    failed_attempts = await self._signup_persistence.record_failed_attempt(
                        str(pending["id"]), self._settings.signup_otp_max_attempts, conn
                    )
                else:
                    employee = await self._employee_persistence.create(
                        employee_code=pending["employee_code"],
                        name=pending["name"],
                        email=email,
                        password_hash=pending["password_hash"],
                        connection=conn,
                    )
                    await self._signup_persistence.delete(str(pending["id"]), conn)
                    return employee
        except asyncpg.UniqueViolationError as exc:
            # Someone registered the same email/employee ID after this signup started.
            constraint = getattr(exc, "constraint_name", "") or ""
            if "employee_code" in constraint:
                raise EmployeeIdAlreadyRegisteredError() from exc
            if "email" in constraint:
                raise EmailAlreadyRegisteredError() from exc
            raise

        remaining = max(self._settings.signup_otp_max_attempts - (failed_attempts or 0), 0)
        if remaining == 0:
            raise InvalidOtpError("The code is incorrect. Too many attempts — request a new code.")
        raise InvalidOtpError(
            f"The code is incorrect. {remaining} attempt{'s' if remaining != 1 else ''} left."
        )

    # ----------------------------------------------------------------- helpers

    def _normalize_email(self, email: str) -> str:
        return str(email).strip().lower()

    def _assert_allowed_domain(self, email: str) -> None:
        allowed = self._settings.signup_allowed_email_domain_list
        if not allowed:
            return
        domain = email.rsplit("@", 1)[-1]
        if domain not in allowed:
            readable = ", ".join(f"@{item}" for item in allowed)
            raise ValidationFailedError(
                "Use your company email address",
                fields=[{"field": "body.email", "message": f"Only {readable} email addresses can sign up"}],
            )

    def _new_otp_expiry(self) -> datetime:
        return self._business_clock.now() + timedelta(minutes=self._settings.signup_otp_ttl_minutes)

    async def _deliver_code(self, email: str, name: str, code: str, otp_hash: str) -> None:
        subject, text_body, html_body = self._compose_email(name, code)
        try:
            await self._email_sender.send(email, subject, text_body, html_body)
        except Exception as exc:
            # The code never reached the user: void it and clear the cooldown so
            # an immediate retry works. A failed cleanup must not mask the error.
            try:
                await self._signup_persistence.release_send_reservation(email, otp_hash)
            except Exception:
                logger.exception("Could not release the send reservation for %s", email)
            if isinstance(exc, EmailDeliveryError):
                raise
            logger.exception("Unexpected error sending signup code to %s", email)
            raise EmailDeliveryError() from exc

    def _compose_email(self, name: str, code: str) -> tuple[str, str, str]:
        minutes = self._settings.signup_otp_ttl_minutes
        subject = f"{code} is your Divine Vision verification code"
        text_body = (
            f"Hi {name},\n\n"
            f"Your verification code is: {code}\n\n"
            f"It expires in {minutes} minutes. If you didn't try to sign up for the "
            f"Divine Vision Employee Portal, you can ignore this email.\n"
        )
        safe_name = html.escape(name)
        html_body = (
            '<div style="font-family:Arial,sans-serif;font-size:15px;color:#1f2937;max-width:480px">'
            f"<p>Hi {safe_name},</p>"
            "<p>Your verification code for the Divine Vision Employee Portal is:</p>"
            f'<p style="font-size:28px;font-weight:bold;letter-spacing:6px;margin:16px 0">{code}</p>'
            f"<p>It expires in {minutes} minutes.</p>"
            '<p style="color:#6b7280;font-size:13px">If you didn\'t try to sign up, you can ignore this email.</p>'
            "</div>"
        )
        return subject, text_body, html_body

    def _to_pending_response(self, row: dict) -> SignupPendingResponse:
        return SignupPendingResponse(
            email=row["email"],
            expires_at=self._business_clock.localize(row["otp_expires_at"]),
        )
