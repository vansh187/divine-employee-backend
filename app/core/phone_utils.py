"""Phone number normalization — the primary duplicate-matching key for Leads (§3, §16.3)."""

import re

from app.core.exceptions import ValidationFailedError

_DIGITS_ONLY = re.compile(r"\D+")


class PhoneNormalizer:
    def normalize(self, raw_phone: str) -> str:
        digits = _DIGITS_ONLY.sub("", raw_phone)

        if digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]

        if len(digits) == 10:
            digits = f"91{digits}"

        if len(digits) != 12 or not digits.startswith("91"):
            raise ValidationFailedError("Phone number must be a valid 10-digit Indian mobile number")

        return f"+{digits}"
