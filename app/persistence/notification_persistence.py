"""All SQL for the `notifications` table."""

from typing import Any

from app.persistence.db_persistence import Database


class NotificationPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        employee_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str | None,
        message: str,
        connection: Any = None,
    ) -> None:
        query = """
            INSERT INTO notifications (employee_id, event_type, entity_type, entity_id, message)
            VALUES ($1, $2, $3, $4, $5)
        """
        params = (employee_id, event_type, entity_type, entity_id, message)
        if connection is not None:
            await connection.execute(query, *params)
            return
        async with self._db.acquire() as conn:
            await conn.execute(query, *params)

    async def list_for_employee(self, employee_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, employee_id, event_type, entity_type, entity_id, message, read_at, created_at
                FROM notifications
                WHERE employee_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                employee_id,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM notifications WHERE employee_id = $1", employee_id
            )
        return [dict(row) for row in rows], total

    async def mark_read(self, notification_id: str, employee_id: str) -> bool:
        async with self._db.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE notifications SET read_at = now()
                WHERE id = $1 AND employee_id = $2 AND read_at IS NULL
                """,
                notification_id,
                employee_id,
            )
        return result.endswith("1")
