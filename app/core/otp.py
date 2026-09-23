"""One-time verification codes: generation and constant-time checking.

Codes are never stored in plaintext. The stored value is an HMAC keyed with the
server secret and bound to the email, so a leaked database row alone can't be
brute-forced back into a usable code (a plain hash of a 6-digit code could be).
"""

import hashlib
import hmac
import secrets

from app.core.config import Settings

OTP_LENGTH = 6


class OtpCodec:
    def __init__(self, settings: Settings) -> None:
        self._key = settings.jwt_secret_key.encode("utf-8")

    def generate(self) -> str:
        """Uniformly random 6-digit code from a CSPRNG (leading zeros kept)."""
        return f"{secrets.randbelow(10**OTP_LENGTH):0{OTP_LENGTH}d}"

    def digest(self, email: str, code: str) -> str:
        message = f"{email}:{code}".encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def matches(self, email: str, code: str, stored_digest: str) -> bool:
        normalized = "".join(code.split())  # tolerate "482 913" pasted from the email
        if len(normalized) != OTP_LENGTH or not normalized.isdigit():
            return False
        return hmac.compare_digest(self.digest(email, normalized), stored_digest)
