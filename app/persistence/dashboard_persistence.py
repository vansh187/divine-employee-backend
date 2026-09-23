"""SQL specific to dashboard aggregation that isn't already covered by another persistence class."""

from app.persistence.db_persistence import Database


class DashboardPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def count_conversions_this_week(self, employee_id: str) -> int:
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COUNT(*) FROM deals
                WHERE (source_owner_employee_id = $1 OR handling_employee_id = $1)
                  AND status = 'COMPLETED'
                  AND updated_at >= date_trunc('week', now())
                """,
                employee_id,
            )
        return int(value)
