"""Business logic for forgot password with an emailed OTP.

Flow: `request_code` emails a 6-digit code (only if an ACTIVE employee has the
email, but always answers the same way) → `verify_code` checks it and hands
out a single-use reset token → `reset_password` sets the new password with
that token and logs the employee out everywhere. No login tokens are issued;
the employee signs in normally afterwards.

Responses never reveal whether an email has an account: unknown emails are
throttled, counted and rejected exactly like known ones, and emails are sent
after the response (through `schedule`), so neither the reply nor its timing
depends on whether a message goes out. A failed send is logged and its code
voided (so an immediate "Resend code" works); it is never reported to the caller.

Failure handling: business-rule failures raise a specific AppError, database
connectivity failures raise ServiceUnavailableError (503), and anything
unexpected is logged and re-raised to the global handler (sanitized 500).
Scheduled email jobs never raise: every failure there is logged.
"""

import asyncio
import hashlib
import html
import logging
import secrets
from collections.abc import Callable
from datetime import timedelta

from app.core.config import Settings
from app.core.email_sender import EmailSender
from app.core.exceptions import (
    AppError,
    InvalidOtpError,
    OtpExpiredError,
    RateLimitExceededError,
    ResetExpiredError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.core.otp import OtpCodec
from app.core.security import PasswordHasher
from app.persistence.db_persistence import Database
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.password_reset_persistence import PasswordResetPersistence
from app.schemas.password_reset_schema import (
    ForgotPasswordResponse,
    ResetPasswordResponse,
    VerifyResetOtpResponse,
)
from app.service.signup_service import _TRANSIENT_DB_ERRORS

logger = logging.getLogger("divine_vision.password_reset_service")

_INVALID_OTP_MESSAGE = "That code is incorrect. Please check and try again."
_OTP_EXPIRED_MESSAGE = "This code has expired. Request a new one."
_THROTTLED_MESSAGE = "Please wait a moment before requesting another code."
_MIN_PASSWORD_LENGTH = 8
# bcrypt only uses the first 72 bytes of a password (same cap as signup).
_BCRYPT_MAX_BYTES = 72
# Compared against when there's no stored code, so a wrong guess costs the same either way.
_NO_CODE_DIGEST = "0" * 64

# Runs `job(*args)` after the response is sent (FastAPI's BackgroundTasks.add_task).
# Pass the async function itself, never a lambda: Starlette runs non-async
# callables in a thread, so a lambda's coroutine would never be awaited.
Scheduler = Callable[..., None]


class PasswordResetService:
    def __init__(
        self,
        db: Database,
        password_reset_persistence: PasswordResetPersistence,
        employee_persistence: EmployeePersistence,
        password_hasher: PasswordHasher,
        otp_codec: OtpCodec,
        email_sender: EmailSender,
        settings: Settings,
    ) -> None:
        self._db = db
        self._password_reset_persistence = password_reset_persistence
        self._employee_persistence = employee_persistence
        self._password_hasher = password_hasher
        self._otp_codec = otp_codec
        self._email_sender = email_sender
        self._settings = settings

    # ----------------------------------------------------------- request code

    async def request_code(
        self, raw_email: str, request_ip: str | None, schedule: Scheduler
    ) -> ForgotPasswordResponse:
        try:
            email = self._normalize_email(raw_email)
            code = self._otp_codec.generate()
            otp_hash = self._otp_codec.digest(email, code)

            reserved = await self._password_reset_persistence.reserve_request(
                email=email,
                otp_hash=otp_hash,
                otp_ttl=timedelta(minutes=self._settings.password_reset_otp_ttl_minutes),
                resend_cooldown=timedelta(seconds=self._settings.password_reset_resend_cooldown_seconds),
                max_requests_per_window=self._settings.password_reset_max_requests_per_hour,
                request_ip=request_ip,
            )
            if reserved is None:
                raise RateLimitExceededError(_THROTTLED_MESSAGE)

            if reserved["employee_id"] is not None:
                schedule(self._deliver_code, email, reserved["name"], code, otp_hash)
            return ForgotPasswordResponse(email=email)
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("Password reset request failed on a transient database error: %s: %s", type(exc).__name__, exc)
            raise ServiceUnavailableError() from exc
        except Exception:
            logger.exception("Unexpected error requesting a password reset code")
            raise

    # ------------------------------------------------------------ verify code

    async def verify_code(self, raw_email: str, otp: str) -> VerifyResetOtpResponse:
        try:
            email = self._normalize_email(raw_email)
            return await self._verify_and_issue_token(email, otp)
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("Password reset code check failed on a transient database error: %s: %s", type(exc).__name__, exc)
            raise ServiceUnavailableError() from exc
        except Exception:
            logger.exception("Unexpected error verifying a password reset code")
            raise

    async def _verify_and_issue_token(self, email: str, otp: str) -> VerifyResetOtpResponse:
        max_attempts = self._settings.password_reset_otp_max_attempts
        async with self._db.transaction() as conn:
            request = await self._password_reset_persistence.lock_for_verify(email, conn)
            if request is None:
                raise InvalidOtpError(_INVALID_OTP_MESSAGE)
            if (
                request["otp_used_at"] is not None
                or request["otp_attempts"] >= max_attempts
                or not request["otp_is_live"]
            ):
                raise OtpExpiredError(_OTP_EXPIRED_MESSAGE)

            stored_digest = request["otp_hash"] or _NO_CODE_DIGEST
            code_matches = self._otp_codec.matches(email, otp, stored_digest)
            if code_matches and request["otp_hash"] is not None and request["employee_id"] is not None:
                reset_token = secrets.token_urlsafe(32)
                await self._password_reset_persistence.issue_reset_token(
                    str(request["id"]),
                    self._hash_reset_token(reset_token),
                    timedelta(minutes=self._settings.password_reset_token_ttl_minutes),
                    conn,
                )
                return VerifyResetOtpResponse(reset_token=reset_token)

            # Must be committed, so it's recorded here and raised after the
            # transaction closes (raising inside would roll it back).
            failed_attempts = await self._password_reset_persistence.record_failed_attempt(
                str(request["id"]), max_attempts, conn
            )

        if failed_attempts >= max_attempts:
            raise OtpExpiredError(_OTP_EXPIRED_MESSAGE)
        raise InvalidOtpError(_INVALID_OTP_MESSAGE)

    # --------------------------------------------------------- reset password

    async def reset_password(self, reset_token: str, new_password: str, schedule: Scheduler) -> ResetPasswordResponse:
        try:
            self._validate_new_password(new_password)
            token_hash = self._hash_reset_token(reset_token)
            # Cheap check first, so a bad token doesn't cost a bcrypt hash.
            if not await self._password_reset_persistence.reset_token_is_live(token_hash):
                raise ResetExpiredError()

            # bcrypt is deliberately slow (~100-300 ms of CPU) — keep it off the event loop.
            password_hash = await asyncio.to_thread(self._password_hasher.hash, new_password)

            async with self._db.transaction() as conn:
                employee_id = await self._password_reset_persistence.consume_reset_token(token_hash, conn)
                if employee_id is None:
                    raise ResetExpiredError()
                employee = await self._employee_persistence.update_password(employee_id, password_hash, conn)
                if employee is None:
                    raise ResetExpiredError()
                await self._employee_persistence.revoke_all_refresh_tokens(employee_id, connection=conn)

            schedule(self._notify_password_changed, employee["email"], employee["name"])
            return ResetPasswordResponse(ok=True)
        except AppError:
            raise
        except _TRANSIENT_DB_ERRORS as exc:
            logger.error("Password reset failed on a transient database error: %s: %s", type(exc).__name__, exc)
            raise ServiceUnavailableError() from exc
        except Exception:
            logger.exception("Unexpected error resetting a password")
            raise

    # ----------------------------------------------------------------- helpers

    def _normalize_email(self, email: str) -> str:
        return str(email).strip().lower()

    def _hash_reset_token(self, reset_token: str) -> str:
        return hashlib.sha256(reset_token.encode("utf-8")).hexdigest()

    def _validate_new_password(self, new_password: str) -> None:
        message = None
        if len(new_password) < _MIN_PASSWORD_LENGTH:
            message = f"Password must be at least {_MIN_PASSWORD_LENGTH} characters."
        elif not new_password.strip():
            message = "Password cannot be only spaces."
        elif len(new_password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            message = f"Password must be at most {_BCRYPT_MAX_BYTES} bytes."
        if message:
            raise ValidationFailedError(message, fields=[{"field": "new_password", "message": message}])

    async def _deliver_code(self, email: str, name: str, code: str, otp_hash: str) -> None:
        """Scheduled job — never raises. On failure the code never reached the
        user: void it and clear the cooldown so an immediate resend works."""
        try:
            subject, text_body, html_body = self._compose_code_email(name, code)
            await self._email_sender.send(email, subject, text_body, html_body)
            return
        except Exception:
            # EmailSender already logged the provider error; the code is never logged.
            logger.error("Password reset code email to %s was not sent; voiding the code", email, exc_info=True)
        try:
            await self._password_reset_persistence.release_send_reservation(email, otp_hash)
        except Exception:
            logger.exception("Could not release the password reset reservation for %s", email)

    async def _notify_password_changed(self, email: str, name: str) -> None:
        """Scheduled job — never raises. The password is already changed, so a
        failed notice is only logged."""
        try:
            subject, text_body, html_body = self._compose_changed_email(name)
            await self._email_sender.send(email, subject, text_body, html_body)
        except Exception:
            logger.warning("Could not send the password-changed notice to %s", email, exc_info=True)

    def _compose_changed_email(self, name: str) -> tuple[str, str, str]:
        # Access tokens are stateless JWTs and stay valid until they expire, so
        # other devices are signed out when their current access token runs out.
        minutes = self._settings.jwt_access_token_expire_minutes
        subject = "Your Divine Vision password was changed"
        text_body = (
            f"Hi {name},\n\n"
            "The password for your Divine Vision Employee Portal account was just changed. "
            f"Any other device signed in to your account will be signed out within {minutes} minutes.\n\n"
            "If you didn't do this, contact your manager or the office right away.\n"
        )
        safe_name = html.escape(name)
        html_body = (
            '<div style="font-family:Arial,sans-serif;font-size:15px;color:#1f2937;max-width:480px">'
            f"<p>Hi {safe_name},</p>"
            "<p>The password for your Divine Vision Employee Portal account was just changed. "
            f"Any other device signed in to your account will be signed out within {minutes} minutes.</p>"
            "<p>If you didn't do this, contact your manager or the office right away.</p>"
            "</div>"
        )
        return subject, text_body, html_body

    def _compose_code_email(self, name: str, code: str) -> tuple[str, str, str]:
        minutes = self._settings.password_reset_otp_ttl_minutes
        subject = "Your Divine Vision password reset code"
        text_body = (
            f"Hi {name},\n\n"
            f"Your password reset code is: {code}\n\n"
            f"It expires in {minutes} minutes.\n\n"
            "If you didn't request this, you can ignore this email.\n"
        )
        safe_name = html.escape(name)
        html_body = (
            '<div style="font-family:Arial,sans-serif;font-size:15px;color:#1f2937;max-width:480px">'
            f"<p>Hi {safe_name},</p>"
            "<p>Your password reset code for the Divine Vision Employee Portal is:</p>"
            f'<p style="font-size:28px;font-weight:bold;letter-spacing:6px;margin:16px 0">{code}</p>'
            f"<p>It expires in {minutes} minutes.</p>"
            '<p style="color:#6b7280;font-size:13px">If you didn\'t request this, you can ignore this email.</p>'
            "</div>"
        )
        return subject, text_body, html_body
