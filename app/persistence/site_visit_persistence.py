"""All SQL for the `site_visits` table."""

from datetime import date, datetime
from typing import Any

from app.persistence.db_persistence import Database


class SiteVisitPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def exists_on_date(self, employee_id: str, target_date: date) -> bool:
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM site_visits
                    WHERE employee_id = $1 AND visit_at::date = $2
                )
                """,
                employee_id,
                target_date,
            )
        return bool(value)

    async def find_by_idempotency_key(self, employee_id: str, idempotency_key: str) -> dict[str, Any] | None:
        """Keys are scoped per employee (uq_site_visits_employee_idempotency_key)."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM site_visits WHERE employee_id = $1 AND idempotency_key = $2",
                employee_id,
                idempotency_key,
            )
        return dict(row) if row else None

    async def create(
        self,
        employee_id: str,
        lead_id: str,
        project_id: str,
        property_id: str | None,
        visit_at: datetime,
        notes: str | None,
        attachments: list[str],
        outcome: str | None,
        idempotency_key: str | None,
        connection: Any,
    ) -> dict[str, Any]:
        row = await connection.fetchrow(
            """
            INSERT INTO site_visits (
                employee_id, lead_id, project_id, property_id, visit_at,
                notes, attachments, outcome, idempotency_key
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
            RETURNING id, employee_id, lead_id, project_id, property_id, visit_at,
                      notes, attachments, outcome, created_at, updated_at
            """,
            employee_id,
            lead_id,
            project_id,
            property_id,
            visit_at,
            notes,
            attachments,
            outcome,
            idempotency_key,
        )
        return dict(row)

    async def get_by_id(self, site_visit_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT sv.id, sv.employee_id, sv.lead_id, sv.project_id, sv.property_id, sv.visit_at,
                       sv.notes, sv.attachments, sv.outcome, sv.created_at, sv.updated_at,
                       l.name AS lead_name, pr.name AS project_name, p.plot_no
                FROM site_visits sv
                JOIN leads l ON l.id = sv.lead_id
                JOIN projects pr ON pr.id = sv.project_id
                LEFT JOIN properties p ON p.id = sv.property_id
                WHERE sv.id = $1
                """,
                site_visit_id,
            )
        return dict(row) if row else None

    async def list_for_employee(
        self, employee_id: str, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sv.id, sv.employee_id, sv.lead_id, sv.project_id, sv.property_id, sv.visit_at,
                       sv.notes, sv.attachments, sv.outcome, sv.created_at, sv.updated_at,
                       l.name AS lead_name, pr.name AS project_name, p.plot_no
                FROM site_visits sv
                JOIN leads l ON l.id = sv.lead_id
                JOIN projects pr ON pr.id = sv.project_id
                LEFT JOIN properties p ON p.id = sv.property_id
                WHERE sv.employee_id = $1
                ORDER BY sv.visit_at DESC
                LIMIT $2 OFFSET $3
                """,
                employee_id,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM site_visits WHERE employee_id = $1", employee_id
            )
        return [dict(row) for row in rows], total

    async def list_today_for_employee(self, employee_id: str, target_date: date) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sv.id, sv.visit_at, sv.outcome, l.name AS lead_name, pr.name AS project_name
                FROM site_visits sv
                JOIN leads l ON l.id = sv.lead_id
                JOIN projects pr ON pr.id = sv.project_id
                WHERE sv.employee_id = $1 AND sv.visit_at::date = $2
                ORDER BY sv.visit_at DESC
                """,
                employee_id,
                target_date,
            )
        return [dict(row) for row in rows]

    async def count_today_for_employee(self, employee_id: str, target_date: date) -> int:
        async with self._db.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM site_visits WHERE employee_id = $1 AND visit_at::date = $2",
                employee_id,
                target_date,
            )
        return int(value)
