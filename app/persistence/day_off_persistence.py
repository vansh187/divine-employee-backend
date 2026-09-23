"""All SQL for the `weekly_day_offs` table."""

from datetime import date
from typing import Any

from app.persistence.db_persistence import Database


class DayOffPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_week(self, employee_id: str, week_key: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, employee_id, week_key, day_off_date, status, selected_at, frozen_at, created_at, updated_at
                FROM weekly_day_offs
                WHERE employee_id = $1 AND week_key = $2
                """,
                employee_id,
                week_key,
            )
        return dict(row) if row else None

    async def is_frozen_on_date(self, employee_id: str, target_date: date) -> bool:
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM weekly_day_offs
                    WHERE employee_id = $1 AND day_off_date = $2 AND status = 'FROZEN'
                )
                """,
                employee_id,
                target_date,
            )
        return bool(value)

    async def count_frozen_in_week(self, employee_id: str, week_key: str) -> int:
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COUNT(*) FROM weekly_day_offs
                WHERE employee_id = $1 AND week_key = $2 AND status IN ('SELECTED', 'FROZEN')
                """,
                employee_id,
                week_key,
            )
        return int(value)

    async def upsert_frozen(
        self, employee_id: str, week_key: str, day_off_date: date
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO weekly_day_offs (employee_id, week_key, day_off_date, status, selected_at, frozen_at)
                VALUES ($1, $2, $3, 'FROZEN', now(), now())
                ON CONFLICT (employee_id, week_key)
                DO UPDATE SET day_off_date = EXCLUDED.day_off_date,
                              status = 'FROZEN',
                              selected_at = now(),
                              frozen_at = now()
                RETURNING id, employee_id, week_key, day_off_date, status, selected_at, frozen_at, created_at, updated_at
                """,
                employee_id,
                week_key,
                day_off_date,
            )
        return dict(row)

    async def list_history(self, employee_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, employee_id, week_key, day_off_date, status, selected_at, frozen_at, created_at, updated_at
                FROM weekly_day_offs
                WHERE employee_id = $1
                ORDER BY day_off_date DESC
                LIMIT $2 OFFSET $3
                """,
                employee_id,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM weekly_day_offs WHERE employee_id = $1", employee_id
            )
        return [dict(row) for row in rows], total
