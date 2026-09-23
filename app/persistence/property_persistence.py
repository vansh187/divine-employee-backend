"""All SQL for `projects` and `properties` tables."""

from typing import Any

from app.persistence.db_persistence import Database


class PropertyPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_projects(self) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, location, phase, status, created_at, updated_at
                FROM projects
                WHERE status = 'ACTIVE'
                ORDER BY name ASC
                """
            )
        return [dict(row) for row in rows]

    async def get_project_by_id(self, project_id: str, connection: Any = None) -> dict[str, Any] | None:
        query = "SELECT id, name, location, phase, status FROM projects WHERE id = $1"
        if connection is not None:
            row = await connection.fetchrow(query, project_id)
        else:
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(query, project_id)
        return dict(row) if row else None

    async def list_inventory(
        self, project_id: str | None, status: str | None, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        conditions = []
        params: list[Any] = []

        if project_id:
            params.append(project_id)
            conditions.append(f"p.project_id = ${len(params)}")
        if status:
            params.append(status)
            conditions.append(f"p.status = ${len(params)}")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT p.id, p.project_id, p.plot_no, p.unit_type, p.area_sqft, p.status,
                       p.created_at, p.updated_at, pr.name AS project_name, pr.location AS project_location
                FROM properties p
                JOIN projects pr ON pr.id = p.project_id
                {where_clause}
                ORDER BY pr.name ASC, p.plot_no ASC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM properties p {where_clause}",
                *params,
            )
        return [dict(row) for row in rows], total

    async def list_properties_by_project(self, project_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, project_id, plot_no, unit_type, area_sqft, status, created_at, updated_at
                FROM properties
                WHERE project_id = $1
                ORDER BY plot_no ASC
                """,
                project_id,
            )
        return [dict(row) for row in rows]

    async def get_property_by_id(self, property_id: str, connection: Any = None) -> dict[str, Any] | None:
        query = """
            SELECT id, project_id, plot_no, unit_type, area_sqft, status, created_at, updated_at
            FROM properties
            WHERE id = $1
            FOR UPDATE
        """
        query_no_lock = query.replace("FOR UPDATE", "")
        if connection is not None:
            row = await connection.fetchrow(query, property_id)
        else:
            async with self._db.acquire() as conn:
                row = await conn.fetchrow(query_no_lock, property_id)
        return dict(row) if row else None

    async def update_status(self, property_id: str, status: str, connection: Any = None) -> None:
        query = "UPDATE properties SET status = $2 WHERE id = $1"
        if connection is not None:
            await connection.execute(query, property_id, status)
            return
        async with self._db.acquire() as conn:
            await conn.execute(query, property_id, status)
