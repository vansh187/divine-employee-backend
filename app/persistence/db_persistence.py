"""Owns the asyncpg connection pool. The ONLY module allowed to talk asyncpg
connection details — every other persistence class receives a `Database`
instance through its constructor and calls `acquire()`/`transaction()`.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.core.config import Settings


class Database:
    """Thin wrapper around an asyncpg pool. One instance lives on `app.state.db`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Creates the pool if it doesn't exist yet. Called lazily on first use, so an
        unreachable database fails individual requests instead of app startup
        (on serverless hosts a failed startup takes down every route, /docs included)."""
        async with self._connect_lock:
            if self._pool is not None:
                return
            self._pool = await asyncpg.create_pool(
                dsn=self._settings.database_url,
                min_size=self._settings.database_pool_min_size,
                max_size=self._settings.database_pool_max_size,
                init=self._init_connection,
            )

    async def _init_connection(self, connection: asyncpg.Connection) -> None:
        # Session timezone = business timezone, so every `timestamptz::date` and
        # `date_trunc(..., now())` buckets by the business day (Asia/Kolkata), not
        # the server default (UTC). Decoded timestamptz values are unaffected.
        # A SET (rather than a startup parameter) also works through Supabase's
        # session-mode pooler.
        await connection.execute(f"SET TIME ZONE '{self._settings.business_timezone}'")
        await connection.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
        )
        await connection.set_type_codec(
            "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
        )
        # Decode uuid columns straight to str so every persistence/service layer
        # can treat ids uniformly without repeated str(...) conversions.
        await connection.set_type_codec(
            "uuid", encoder=str, decoder=str, schema="pg_catalog", format="text"
        )

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.connect()
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                yield connection
