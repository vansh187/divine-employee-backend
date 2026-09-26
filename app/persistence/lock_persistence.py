"""All SQL for `lead_locks` and `property_locks` — the V1 global 3-day protection engine (§5)."""

from datetime import datetime
from typing import Any

from app.persistence.db_persistence import Database
from app.persistence.waitlist_persistence import WaitlistPersistence


class LockPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._waitlist = WaitlistPersistence(db)

    async def get_active_lead_lock(self, lead_id: str, connection: Any) -> dict[str, Any] | None:
        """`FOR UPDATE` only holds a row lock for the life of a transaction — the
        caller MUST supply a connection obtained from `Database.transaction()`.
        There is deliberately no no-connection fallback: a bare `acquire()` would
        release the lock the instant this query returns, making the enforcement
        it exists for a no-op.

        A lock past `expires_at` is expired here, in the caller's transaction,
        before the lookup — so correctness never depends on when the periodic
        sweep last ran (on serverless hosts it may not have run for a while).
        """
        await connection.execute(
            "UPDATE lead_locks SET status = 'EXPIRED' WHERE lead_id = $1 AND status = 'ACTIVE' AND expires_at <= now()",
            lead_id,
        )
        row = await connection.fetchrow(
            """
            SELECT id, lead_id, employee_id, site_visit_id, locked_at, expires_at, status
            FROM lead_locks
            WHERE lead_id = $1 AND status = 'ACTIVE'
            FOR UPDATE
            """,
            lead_id,
        )
        return dict(row) if row else None

    async def get_active_property_lock(self, property_id: str, connection: Any) -> dict[str, Any] | None:
        """See `get_active_lead_lock` — `connection` must come from a transaction,
        and a lock past `expires_at` is expired first (reverting a plain LOCKED plot)."""
        expired_count = await connection.fetchval(
            """
            WITH expired AS (
                UPDATE property_locks SET status = 'EXPIRED'
                WHERE property_id = $1 AND status = 'ACTIVE' AND expires_at <= now()
                RETURNING 1
            )
            SELECT count(*) FROM expired
            """,
            property_id,
        )
        if expired_count:
            await connection.execute(
                "UPDATE properties SET status = 'AVAILABLE' WHERE id = $1 AND status = 'LOCKED'", property_id
            )
            await self._waitlist.promote_next_for_available_plots([property_id], connection)
        row = await connection.fetchrow(
            """
            SELECT id, property_id, employee_id, site_visit_id, lead_lock_id, locked_at, expires_at, status
            FROM property_locks
            WHERE property_id = $1 AND status = 'ACTIVE'
            FOR UPDATE
            """,
            property_id,
        )
        return dict(row) if row else None

    async def create_lead_lock(
        self, lead_id: str, employee_id: str, site_visit_id: str, expires_at: datetime, connection: Any
    ) -> dict[str, Any]:
        row = await connection.fetchrow(
            """
            INSERT INTO lead_locks (lead_id, employee_id, site_visit_id, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id, lead_id, employee_id, site_visit_id, locked_at, expires_at, status
            """,
            lead_id,
            employee_id,
            site_visit_id,
            expires_at,
        )
        return dict(row)

    async def create_property_lock(
        self,
        property_id: str,
        employee_id: str,
        site_visit_id: str,
        lead_lock_id: str,
        expires_at: datetime,
        connection: Any,
    ) -> dict[str, Any]:
        row = await connection.fetchrow(
            """
            INSERT INTO property_locks (property_id, employee_id, site_visit_id, lead_lock_id, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, property_id, employee_id, site_visit_id, lead_lock_id, locked_at, expires_at, status
            """,
            property_id,
            employee_id,
            site_visit_id,
            lead_lock_id,
            expires_at,
        )
        return dict(row)

    async def renew_lead_lock(self, lock_id: str, new_expires_at: datetime, connection: Any = None) -> None:
        query = "UPDATE lead_locks SET expires_at = GREATEST(expires_at, $2) WHERE id = $1 AND status = 'ACTIVE'"
        if connection is not None:
            await connection.execute(query, lock_id, new_expires_at)
            return
        async with self._db.acquire() as conn:
            await conn.execute(query, lock_id, new_expires_at)

    async def renew_property_lock(self, lock_id: str, new_expires_at: datetime, connection: Any) -> None:
        await connection.execute(
            "UPDATE property_locks SET expires_at = GREATEST(expires_at, $2) WHERE id = $1 AND status = 'ACTIVE'", lock_id, new_expires_at
        )

    async def renew_property_locks_for_lead_lock(
        self, lead_lock_id: str, new_expires_at: datetime, connection: Any = None
    ) -> None:
        query = "UPDATE property_locks SET expires_at = GREATEST(expires_at, $2) WHERE lead_lock_id = $1 AND status = 'ACTIVE'"
        if connection is not None:
            await connection.execute(query, lead_lock_id, new_expires_at)
            return
        async with self._db.acquire() as conn:
            await conn.execute(query, lead_lock_id, new_expires_at)

    async def extend_locks_for_lead(
        self, lead_id: str, property_id: str | None, new_expires_at: datetime, connection: Any
    ) -> None:
        """Longer hold for a booking in progress: the lead's lock and the lock on `property_id` only
        (the lead's other plots keep their own opportunity's window). Never shortens a lock."""
        if property_id is not None:
            await connection.execute(
                """
                UPDATE property_locks pl SET expires_at = GREATEST(pl.expires_at, $3)
                FROM lead_locks ll
                WHERE pl.lead_lock_id = ll.id AND ll.lead_id = $1 AND ll.status = 'ACTIVE'
                  AND pl.property_id = $2 AND pl.status = 'ACTIVE'
                """,
                lead_id,
                property_id,
                new_expires_at,
            )
        await connection.execute(
            "UPDATE lead_locks SET expires_at = GREATEST(expires_at, $2) WHERE lead_id = $1 AND status = 'ACTIVE'",
            lead_id,
            new_expires_at,
        )

    async def release_property_lock_for_lead(self, lead_id: str, property_id: str, connection: Any) -> None:
        """Frees a plot held for `lead_id`; a DEAL_LOCKED or SOLD plot is never downgraded."""
        await connection.execute(
            """
            WITH released AS (
                UPDATE property_locks pl SET status = 'RELEASED'
                FROM lead_locks ll
                WHERE pl.lead_lock_id = ll.id AND ll.lead_id = $1 AND pl.property_id = $2 AND pl.status = 'ACTIVE'
                RETURNING pl.property_id
            )
            UPDATE properties SET status = 'AVAILABLE'
            WHERE id IN (SELECT property_id FROM released) AND status = 'LOCKED'
            """,
            lead_id,
            property_id,
        )
        await self._waitlist.promote_next_for_available_plots([property_id], connection)

    async def release_expired_locks(self) -> tuple[int, int]:
        """Server-side sweep: expire locks past `expires_at`. Called by a scheduled job/endpoint.

        Requirement §5: "If no qualifying action occurs before expiry, locks
        release automatically through backend processing... On expiry the
        Property returns to the shared employee inventory." A DEAL_LOCKED or
        SOLD property is never downgraded here — only a plain LOCKED one reverts.
        """
        async with self._db.transaction() as conn:
            lead_result = await conn.execute(
                "UPDATE lead_locks SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND expires_at <= now()"
            )
            expired_property_ids = await conn.fetch(
                "SELECT property_id FROM property_locks WHERE status = 'ACTIVE' AND expires_at <= now()"
            )
            property_result = await conn.execute(
                "UPDATE property_locks SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND expires_at <= now()"
            )
            if expired_property_ids:
                property_ids = [row["property_id"] for row in expired_property_ids]
                await conn.execute(
                    "UPDATE properties SET status = 'AVAILABLE' WHERE id = ANY($1::uuid[]) AND status = 'LOCKED'",
                    property_ids,
                )
                await self._waitlist.promote_next_for_available_plots(
                    [str(property_id) for property_id in property_ids], conn
                )
        return self._extract_count(lead_result), self._extract_count(property_result)

    async def list_active_locks_for_employee(self, employee_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ll.id AS lead_lock_id, ll.lead_id, ll.expires_at, l.name AS lead_name,
                       p.id AS property_id, p.plot_no, pr.name AS project_name
                FROM lead_locks ll
                JOIN leads l ON l.id = ll.lead_id
                LEFT JOIN property_locks pl
                    ON pl.lead_lock_id = ll.id AND pl.status = 'ACTIVE' AND pl.expires_at > now()
                LEFT JOIN properties p ON p.id = pl.property_id
                LEFT JOIN projects pr ON pr.id = p.project_id
                WHERE ll.employee_id = $1 AND ll.status = 'ACTIVE' AND ll.expires_at > now()
                ORDER BY ll.expires_at ASC
                """,
                employee_id,
            )
        return [dict(row) for row in rows]

    def _extract_count(self, execute_result: str) -> int:
        # asyncpg execute() returns strings like "UPDATE 3"
        parts = execute_result.split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0
