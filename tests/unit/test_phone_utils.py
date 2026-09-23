"""Unit tests for phone normalization — the primary Lead dedup key (§3, §16.3)."""

import pytest

from app.core.exceptions import ValidationFailedError
from app.core.phone_utils import PhoneNormalizer


@pytest.fixture
def normalizer() -> PhoneNormalizer:
    return PhoneNormalizer()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("9876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("91-9876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("  9876543210  ", "+919876543210"),
    ],
)
def test_normalize_valid_variants_converge_to_same_key(normalizer: PhoneNormalizer, raw: str, expected: str) -> None:
    assert normalizer.normalize(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "", "abcdefghij", "999999999999999"])
def test_normalize_rejects_invalid_numbers(normalizer: PhoneNormalizer, raw: str) -> None:
    with pytest.raises(ValidationFailedError):
        normalizer.normalize(raw)
