"""All SQL for `deals` and `deal_locks` (§20.6 — Opportunity → confirmed Deal, hard lock)."""

from typing import Any

from app.persistence.db_persistence import Database


class DealPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_active_lock_for_property(self, property_id: str, connection: Any) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            """
            SELECT id, deal_id, property_id, locked_at, released_at, status
            FROM deal_locks
            WHERE property_id = $1 AND status = 'ACTIVE'
            FOR UPDATE
            """,
            property_id,
        )
        return dict(row) if row else None

    async def create_deal(
        self,
        opportunity_id: str,
        lead_id: str,
        project_id: str,
        property_id: str,
        source_owner_type: str,
        source_owner_employee_id: str | None,
        source_owner_channel_partner_id: str | None,
        handling_employee_id: str | None,
        connection: Any,
    ) -> dict[str, Any]:
        row = await connection.fetchrow(
            """
            INSERT INTO deals (
                opportunity_id, lead_id, project_id, property_id, source_owner_type,
                source_owner_employee_id, source_owner_channel_partner_id, handling_employee_id, status
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'CONFIRMED')
            RETURNING id, opportunity_id, lead_id, project_id, property_id, source_owner_type,
                      source_owner_employee_id, source_owner_channel_partner_id, handling_employee_id,
                      status, created_at, updated_at
            """,
            opportunity_id,
            lead_id,
            project_id,
            property_id,
            source_owner_type,
            source_owner_employee_id,
            source_owner_channel_partner_id,
            handling_employee_id,
        )
        return dict(row)

    async def create_deal_lock(self, deal_id: str, property_id: str, connection: Any) -> dict[str, Any]:
        row = await connection.fetchrow(
            """
            INSERT INTO deal_locks (deal_id, property_id, status)
            VALUES ($1, $2, 'ACTIVE')
            RETURNING id, deal_id, property_id, locked_at, released_at, status
            """,
            deal_id,
            property_id,
        )
        return dict(row)

    async def release_deal_lock(self, deal_id: str, connection: Any) -> None:
        await connection.execute(
            "UPDATE deal_locks SET status = 'RELEASED', released_at = now() WHERE deal_id = $1 AND status = 'ACTIVE'",
            deal_id,
        )

    async def update_deal_status(
        self, deal_id: str, status: str, connection: Any, expected_current_statuses: list[str]
    ) -> dict[str, Any] | None:
        """Atomically transitions the deal only if it is currently in one of
        `expected_current_statuses` — guards against re-completing/re-cancelling
        a deal that already left the CONFIRMED state (§20.6 state machine).
        Returns None (no row matched) if the current status doesn't qualify.
        """
        row = await connection.fetchrow(
            """
            UPDATE deals SET status = $2 WHERE id = $1 AND status = ANY($3::deal_status[])
            RETURNING id, opportunity_id, lead_id, project_id, property_id, source_owner_type,
                      source_owner_employee_id, source_owner_channel_partner_id, handling_employee_id,
                      status, created_at, updated_at
            """,
            deal_id,
            status,
            expected_current_statuses,
        )
        return dict(row) if row else None

    async def get_by_id(self, deal_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, opportunity_id, lead_id, project_id, property_id, source_owner_type,
                       source_owner_employee_id, source_owner_channel_partner_id, handling_employee_id,
                       status, created_at, updated_at
                FROM deals
                WHERE id = $1
                """,
                deal_id,
            )
        return dict(row) if row else None

    async def list_for_employee(self, employee_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, opportunity_id, lead_id, project_id, property_id, source_owner_type,
                       source_owner_employee_id, source_owner_channel_partner_id, handling_employee_id,
                       status, created_at, updated_at
                FROM deals
                WHERE source_owner_employee_id = $1 OR handling_employee_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                employee_id,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM deals WHERE source_owner_employee_id = $1 OR handling_employee_id = $1",
                employee_id,
            )
        return [dict(row) for row in rows], total
