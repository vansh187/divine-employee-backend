"""In-memory sliding-window rate limiter.

Default policy (configurable via .env): 15 requests per 60 seconds per
(client identity, route) pair. Client identity is the authenticated
employee_id when available, else the caller's IP address, so limits apply
per real caller rather than per shared proxy IP.
"""

import asyncio
import time

from fastapi import Request

from app.core.config import Settings
from app.core.exceptions import RateLimitExceededError
from app.core.security import TOKEN_TYPE_ACCESS, TokenService


class RateLimiter:
    """Single shared instance stored on `app.state`; used as a FastAPI dependency."""

    def __init__(self, settings: Settings) -> None:
        self._max_requests = settings.rate_limit_max_requests
        self._window_seconds = settings.rate_limit_window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()
        self._token_service = TokenService(settings)

    async def check(self, request: Request) -> None:
        identity = self._resolve_identity(request)
        bucket_key = f"{identity}:{request.scope.get('route').path if request.scope.get('route') else request.url.path}"
        now = time.monotonic()
        window_start = now - self._window_seconds

        async with self._lock:
            timestamps = [ts for ts in self._hits.get(bucket_key, []) if ts > window_start]
            if len(timestamps) >= self._max_requests:
                self._hits[bucket_key] = timestamps
                raise RateLimitExceededError(
                    f"Rate limit of {self._max_requests} requests per {self._window_seconds}s exceeded"
                )
            timestamps.append(now)
            self._hits[bucket_key] = timestamps
            if len(self._hits) > 10_000:
                self._evict_idle_buckets(window_start)

    def _evict_idle_buckets(self, window_start: float) -> None:
        """Bounds unbounded memory growth from ever-accumulating bucket keys
        (e.g. many distinct IPs hitting login) — called opportunistically once
        the bucket count passes a threshold rather than on every request."""
        idle_keys = [key for key, timestamps in self._hits.items() if not any(ts > window_start for ts in timestamps)]
        for key in idle_keys:
            del self._hits[key]

    def _resolve_identity(self, request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            raw_token = authorization[7:].strip()
            try:
                # Decode to the stable employee id so a token refresh mid-session
                # doesn't reset the caller's rate-limit window (a fresh JWT string
                # would otherwise look like a brand-new, unrelated caller).
                payload = self._token_service.decode_token(raw_token, expected_type=TOKEN_TYPE_ACCESS)
                return f"employee:{payload['sub']}"
            except Exception:
                # Malformed/expired/wrong-type token: never let identity resolution
                # itself raise. Fall through to the IP bucket — bucketing by the raw
                # token text would let a caller mint a fresh bucket per request.
                pass
        client = request.client
        return client.host if client else "unknown"


async def enforce_rate_limit(request: Request) -> None:
    """FastAPI dependency entrypoint — delegates to the limiter on app.state."""
    limiter: RateLimiter = request.app.state.rate_limiter
    await limiter.check(request)
