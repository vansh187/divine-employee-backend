"""Owns the asyncpg connection pool. The ONLY module allowed to talk asyncpg
connection details — every other persistence class receives a `Database`
instance through its constructor and calls `acquire()`/`transaction()`.
"""

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

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._settings.database_url,
            min_size=self._settings.database_pool_min_size,
            max_size=self._settings.database_pool_max_size,
            init=self._init_connection,
            # Session timezone = business timezone, so every `timestamptz::date`
            # and `now()::date` buckets by the business day (Asia/Kolkata), not
            # the server default (UTC). Decoded timestamptz values are unaffected.
            server_settings={"timezone": self._settings.business_timezone},
        )

    async def _init_connection(self, connection: asyncpg.Connection) -> None:
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

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool is not initialized")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                yield connection
