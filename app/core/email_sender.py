"""Outgoing email. Services depend on the `EmailSender` protocol, never on SMTP
directly, so delivery can be swapped (or faked in tests) without touching them.

Every delivery failure — misconfiguration, DNS/network errors, timeouts, auth
or recipient rejection by the provider — surfaces as `EmailDeliveryError`
(503 EMAIL_DELIVERY_FAILED). Provider error text goes to the server log only.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from app.core.config import Settings
from app.core.exceptions import EmailDeliveryError

logger = logging.getLogger("divine_vision.email_sender")


class EmailSender(Protocol):
    async def send(self, to_address: str, subject: str, text_body: str, html_body: str) -> None: ...


class SmtpEmailSender:
    """SMTP over implicit TLS (port 465) or STARTTLS (587). Works with Resend,
    Google Workspace, Zoho, Amazon SES and any standard SMTP relay."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ssl_context = ssl.create_default_context()

    async def send(self, to_address: str, subject: str, text_body: str, html_body: str) -> None:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST or RESEND_API_KEY", self._settings.effective_smtp_host),
                ("SMTP_USERNAME or RESEND_API_KEY", self._settings.effective_smtp_username),
                ("SMTP_PASSWORD or RESEND_API_KEY", self._settings.effective_smtp_password),
                ("EMAIL_FROM_ADDRESS or DIVINE_RESEND_EMAIL", self._settings.email_from_address),
            )
            if not value
        ]
        if missing:
            logger.error("Email delivery is not configured; missing: %s", ", ".join(missing))
            raise EmailDeliveryError()

        message = self._build_message(to_address, subject, text_body, html_body)
        try:
            # smtplib is blocking — keep it off the event loop. The outer
            # wait_for is a backstop in case the socket timeout doesn't fire.
            await asyncio.wait_for(
                asyncio.to_thread(self._send_blocking, message),
                timeout=self._settings.smtp_timeout_seconds + 5,
            )
        except EmailDeliveryError:
            raise
        except (smtplib.SMTPException, OSError, asyncio.TimeoutError, TimeoutError) as exc:
            logger.error("Email delivery to %s failed: %s: %s", to_address, type(exc).__name__, exc)
            raise EmailDeliveryError() from exc
        except Exception as exc:
            logger.exception("Unexpected email delivery failure to %s", to_address)
            raise EmailDeliveryError() from exc

    def _build_message(self, to_address: str, subject: str, text_body: str, html_body: str) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self._settings.email_from_name, self._settings.email_from_address or ""))
        message["To"] = to_address
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        return message

    def _send_blocking(self, message: EmailMessage) -> None:
        host = self._settings.effective_smtp_host or ""
        port = self._settings.smtp_port
        timeout = self._settings.smtp_timeout_seconds
        username = self._settings.effective_smtp_username or ""
        password = self._settings.effective_smtp_password or ""
        if self._settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=self._ssl_context) as client:
                client.login(username, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.starttls(context=self._ssl_context)
                client.login(username, password)
                client.send_message(message)


class LogEmailSender:
    """Development only: writes the email to the server log instead of sending it."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, to_address: str, subject: str, text_body: str, html_body: str) -> None:
        logger.warning("EMAIL_DELIVERY_MODE=log — not sending. To: %s | Subject: %s\n%s", to_address, subject, text_body)


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_delivery_mode == "log":
        return LogEmailSender(settings)
    return SmtpEmailSender(settings)
