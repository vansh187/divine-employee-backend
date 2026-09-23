"""Unit tests for signup building blocks that need no database or network."""

import pytest

from app.core.config import get_settings
from app.core.email_sender import LogEmailSender, SmtpEmailSender, build_email_sender
from app.core.exceptions import EmailDeliveryError
from app.core.otp import OtpCodec


def test_generated_codes_are_six_digits_and_vary() -> None:
    codec = OtpCodec(get_settings())
    codes = {codec.generate() for _ in range(200)}
    assert all(len(code) == 6 and code.isdigit() for code in codes)
    assert len(codes) > 150


def test_code_matching_is_bound_to_the_email() -> None:
    codec = OtpCodec(get_settings())
    digest = codec.digest("a@divinevisioninfra.com", "482913")
    assert codec.matches("a@divinevisioninfra.com", "482913", digest)
    assert codec.matches("a@divinevisioninfra.com", " 482 913 ", digest)
    assert not codec.matches("b@divinevisioninfra.com", "482913", digest)
    assert not codec.matches("a@divinevisioninfra.com", "482914", digest)
    for malformed in ("", "48291", "4829130", "abcdef", "48291x"):
        assert not codec.matches("a@divinevisioninfra.com", malformed, digest)
    assert "482913" not in digest


def test_allowed_domain_list_parsing() -> None:
    settings = get_settings().model_copy(update={"signup_allowed_email_domains": " @DivineVisionInfra.com , ,x.org"})
    assert settings.signup_allowed_email_domain_list == ["divinevisioninfra.com", "x.org"]
    assert get_settings().model_copy(update={"signup_allowed_email_domains": ""}).signup_allowed_email_domain_list == []


def test_allowed_email_list_parsing() -> None:
    settings = get_settings().model_copy(update={"signup_allowed_emails": " Tester@Gmail.com, ,qa@x.org "})
    assert settings.signup_allowed_email_list == ["tester@gmail.com", "qa@x.org"]
    assert get_settings().model_copy(update={"signup_allowed_emails": ""}).signup_allowed_email_list == []


def test_resend_api_key_fills_in_smtp_settings() -> None:
    settings = get_settings().model_copy(
        update={"resend_api_key": "re_test", "smtp_host": None, "smtp_username": None, "smtp_password": None}
    )
    assert settings.effective_smtp_host == "smtp.resend.com"
    assert settings.effective_smtp_username == "resend"
    assert settings.effective_smtp_password == "re_test"

    explicit = settings.model_copy(update={"smtp_host": "smtp.example.com", "smtp_password": "p"})
    assert explicit.effective_smtp_host == "smtp.example.com"
    assert explicit.effective_smtp_password == "p"


def test_sender_is_chosen_by_delivery_mode() -> None:
    assert isinstance(build_email_sender(get_settings().model_copy(update={"email_delivery_mode": "log"})), LogEmailSender)
    assert isinstance(build_email_sender(get_settings().model_copy(update={"email_delivery_mode": "smtp"})), SmtpEmailSender)


async def test_unconfigured_smtp_fails_cleanly_without_network() -> None:
    settings = get_settings().model_copy(
        update={"resend_api_key": None, "smtp_host": None, "smtp_username": None, "smtp_password": None}
    )
    with pytest.raises(EmailDeliveryError):
        await SmtpEmailSender(settings).send("a@b.com", "s", "t", "<p>h</p>")


async def test_unreachable_smtp_host_fails_cleanly() -> None:
    settings = get_settings().model_copy(
        update={
            "smtp_host": "nonexistent.invalid",
            "smtp_username": "u",
            "smtp_password": "p",
            "email_from_address": "noreply@divinevisioninfra.com",
            "smtp_timeout_seconds": 3,
        }
    )
    with pytest.raises(EmailDeliveryError):
        await SmtpEmailSender(settings).send("a@b.com", "s", "t", "<p>h</p>")
