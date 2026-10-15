"""Email-domain policy for employee authentication."""

COMPANY_EMAIL_DOMAIN = "divinevisioninfra.com"


def is_company_email(email: str) -> bool:
    """Return whether an address belongs to the company's exact email domain."""
    normalized = str(email).strip().lower()
    local_part, separator, domain = normalized.rpartition("@")
    return bool(local_part and separator and domain == COMPANY_EMAIL_DOMAIN)
