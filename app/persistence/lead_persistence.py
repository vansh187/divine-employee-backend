"""All SQL for the `leads` table."""

from datetime import datetime
from typing import Any

from app.persistence.db_persistence import Database


class LeadPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_normalized_phone(self, normalized_phone: str, connection: Any = None) -> dict[str, Any] | None:
        query = """
            SELECT id, name, normalized_phone, raw_phone, email, source, originating_employee_id,
                   current_employee_id, lifecycle_status, first_visit_at, latest_visit_at, created_at, updated_at
            FROM leads
            WHERE normalized_phone = $1
        """
        if connection is not None:
            row = await connection.fetchrow(query, normalized_phone)
        else:
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(query, normalized_phone)
        return dict(row) if row else None

    async def get_by_id(self, lead_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, normalized_phone, raw_phone, email, source, originating_employee_id,
                       current_employee_id, lifecycle_status, first_visit_at, latest_visit_at, created_at, updated_at
                FROM leads
                WHERE id = $1
                """,
                lead_id,
            )
        return dict(row) if row else None

    async def get_or_create(
        self,
        name: str,
        normalized_phone: str,
        raw_phone: str | None,
        email: str | None,
        originating_employee_id: str,
        visit_at: datetime,
        connection: Any,
    ) -> dict[str, Any]:
        """Race-safe lead resolution: if a concurrent transaction inserts the same
        phone first, ON CONFLICT waits for it and we read its row instead of
        failing on the unique constraint."""
        row = await connection.fetchrow(
            """
            INSERT INTO leads (
                name, normalized_phone, raw_phone, email, source,
                originating_employee_id, current_employee_id, lifecycle_status,
                first_visit_at, latest_visit_at
            )
            VALUES ($1, $2, $3, $4, 'EMPLOYEE_SITE_VISIT', $5, $5, 'NEW', $6, $6)
            ON CONFLICT (normalized_phone) DO NOTHING
            RETURNING id, name, normalized_phone, raw_phone, email, source, originating_employee_id,
                      current_employee_id, lifecycle_status, first_visit_at, latest_visit_at, created_at, updated_at
            """,
            name, normalized_phone, raw_phone, email, originating_employee_id, visit_at,
        )
        if row is not None:
            return dict(row)
        return await self.get_by_normalized_phone(normalized_phone, connection=connection)

    async def touch_latest_visit(
        self, lead_id: str, current_employee_id: str, visit_at: datetime, connection: Any = None
    ) -> None:
        query = """
            UPDATE leads
            SET latest_visit_at = $2,
                current_employee_id = $3,
                lifecycle_status = CASE WHEN lifecycle_status = 'NEW' THEN 'IN_PROGRESS' ELSE lifecycle_status END
            WHERE id = $1
        """
        params = (lead_id, visit_at, current_employee_id)
        if connection is not None:
            await connection.execute(query, *params)
            return
        async with self._db.acquire() as conn:
            await conn.execute(query, *params)

    async def search(
        self, employee_id: str, query_text: str | None, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            if query_text:
                like_pattern = f"%{query_text}%"
                rows = await conn.fetch(
                    """
                    SELECT id, name, normalized_phone, raw_phone, email, source, originating_employee_id,
                           current_employee_id, lifecycle_status, first_visit_at, latest_visit_at, created_at, updated_at
                    FROM leads
                    WHERE current_employee_id = $1 AND (name ILIKE $2 OR normalized_phone ILIKE $2)
                    ORDER BY latest_visit_at DESC NULLS LAST
                    LIMIT $3 OFFSET $4
                    """,
                    employee_id,
                    like_pattern,
                    page_size,
                    offset,
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM leads
                    WHERE current_employee_id = $1 AND (name ILIKE $2 OR normalized_phone ILIKE $2)
                    """,
                    employee_id,
                    like_pattern,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, name, normalized_phone, raw_phone, email, source, originating_employee_id,
                           current_employee_id, lifecycle_status, first_visit_at, latest_visit_at, created_at, updated_at
                    FROM leads
                    WHERE current_employee_id = $1
                    ORDER BY latest_visit_at DESC NULLS LAST
                    LIMIT $2 OFFSET $3
                    """,
                    employee_id,
                    page_size,
                    offset,
                )
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM leads WHERE current_employee_id = $1", employee_id
                )
        return [dict(row) for row in rows], total

    async def list_due_follow_ups(self, employee_id: str, limit: int) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT l.id, l.name, l.normalized_phone, l.lifecycle_status, l.latest_visit_at,
                       ll.expires_at
                FROM leads l
                JOIN lead_locks ll ON ll.lead_id = l.id AND ll.status = 'ACTIVE' AND ll.expires_at > now()
                WHERE ll.employee_id = $1
                ORDER BY ll.expires_at ASC
                LIMIT $2
                """,
                employee_id,
                limit,
            )
        return [dict(row) for row in rows]
