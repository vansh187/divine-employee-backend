"""All SQL for the `attendance_records` table."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.persistence.db_persistence import Database


class AttendancePersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_date(self, employee_id: str, work_date: date) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, employee_id, work_date, check_in_at, check_out_at, status,
                       check_in_lat, check_in_lng, created_at, updated_at
                FROM attendance_records
                WHERE employee_id = $1 AND work_date = $2
                """,
                employee_id,
                work_date,
            )
        return dict(row) if row else None

    async def create_check_in(
        self,
        employee_id: str,
        work_date: date,
        check_in_at: datetime,
        status: str,
        check_in_lat: Decimal | None,
        check_in_lng: Decimal | None,
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO attendance_records (employee_id, work_date, check_in_at, status, check_in_lat, check_in_lng)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, employee_id, work_date, check_in_at, check_out_at, status,
                          check_in_lat, check_in_lng, created_at, updated_at
                """,
                employee_id,
                work_date,
                check_in_at,
                status,
                check_in_lat,
                check_in_lng,
            )
        return dict(row)

    async def update_check_out(
        self, employee_id: str, work_date: date, check_out_at: datetime, status: str
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE attendance_records
                SET check_out_at = $3, status = $4
                WHERE employee_id = $1 AND work_date = $2
                RETURNING id, employee_id, work_date, check_in_at, check_out_at, status,
                          check_in_lat, check_in_lng, created_at, updated_at
                """,
                employee_id,
                work_date,
                check_out_at,
                status,
            )
        return dict(row)

    async def list_history(self, employee_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, employee_id, work_date, check_in_at, check_out_at, status,
                       check_in_lat, check_in_lng, created_at, updated_at
                FROM attendance_records
                WHERE employee_id = $1
                ORDER BY work_date DESC
                LIMIT $2 OFFSET $3
                """,
                employee_id,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM attendance_records WHERE employee_id = $1", employee_id
            )
        return [dict(row) for row in rows], total

    async def list_for_month(self, employee_id: str, year: int, month: int) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, employee_id, work_date, check_in_at, check_out_at, status,
                       check_in_lat, check_in_lng, created_at, updated_at
                FROM attendance_records
                WHERE employee_id = $1
                  AND date_part('year', work_date) = $2
                  AND date_part('month', work_date) = $3
                ORDER BY work_date ASC
                """,
                employee_id,
                year,
                month,
            )
        return [dict(row) for row in rows]
