"""All SQL for the `follow_up_actions` table."""

from typing import Any

from app.persistence.db_persistence import Database


class FollowUpPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        lead_id: str,
        employee_id: str,
        action_type: str,
        notes: str | None,
        reference: str | None,
        connection: Any = None,
    ) -> dict[str, Any]:
        query = """
            INSERT INTO follow_up_actions (lead_id, employee_id, action_type, notes, reference)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, lead_id, employee_id, action_type, notes, reference, logged_at, created_at
        """
        params = (lead_id, employee_id, action_type, notes, reference)
        if connection is not None:
            row = await connection.fetchrow(query, *params)
        else:
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(query, *params)
        return dict(row)

    async def list_for_lead(self, lead_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, lead_id, employee_id, action_type, notes, reference, logged_at, created_at
                FROM follow_up_actions
                WHERE lead_id = $1
                ORDER BY logged_at DESC
                """,
                lead_id,
            )
        return [dict(row) for row in rows]
