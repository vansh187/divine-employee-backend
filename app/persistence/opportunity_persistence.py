"""All SQL for `opportunities`, `opportunity_claims` and `opportunity_resolutions` (REQ-25)."""

from datetime import datetime
from typing import Any

from app.persistence.db_persistence import Database


class OpportunityPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_active_by_lead_and_property(
        self, lead_id: str, property_id: str, connection: Any
    ) -> dict[str, Any] | None:
        # Expire a lapsed opportunity first (same rule as the periodic sweep) so a
        # stale ACTIVE row never produces a false attribution conflict.
        await connection.execute(
            """
            UPDATE opportunities SET status = 'EXPIRED'
            WHERE lead_id = $1 AND property_id = $2 AND status = 'ACTIVE' AND expires_at <= now()
            """,
            lead_id,
            property_id,
        )
        row = await connection.fetchrow(
            """
            SELECT id, lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
                   source_owner_channel_partner_id, handling_employee_id, source, status,
                   attribution_status, locked_at, expires_at
            FROM opportunities
            WHERE lead_id = $1 AND property_id = $2 AND status IN ('ACTIVE', 'ATTRIBUTION_CONFLICT')
            FOR UPDATE
            """,
            lead_id,
            property_id,
        )
        return dict(row) if row else None

    async def get_active_by_lead_and_project_no_property(
        self, lead_id: str, project_id: str, connection: Any
    ) -> dict[str, Any] | None:
        await connection.execute(
            """
            UPDATE opportunities SET status = 'EXPIRED'
            WHERE lead_id = $1 AND project_id = $2 AND property_id IS NULL
              AND status = 'ACTIVE' AND expires_at <= now()
            """,
            lead_id,
            project_id,
        )
        row = await connection.fetchrow(
            """
            SELECT id, lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
                   source_owner_channel_partner_id, handling_employee_id, source, status,
                   attribution_status, locked_at, expires_at
            FROM opportunities
            WHERE lead_id = $1 AND project_id = $2 AND property_id IS NULL AND status IN ('ACTIVE', 'ATTRIBUTION_CONFLICT')
            FOR UPDATE
            """,
            lead_id,
            project_id,
        )
        return dict(row) if row else None

    async def create(
        self,
        lead_id: str,
        project_id: str,
        property_id: str | None,
        source_owner_employee_id: str,
        handling_employee_id: str,
        expires_at: datetime,
        connection: Any,
    ) -> dict[str, Any]:
        row = await connection.fetchrow(
            """
            INSERT INTO opportunities (
                lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
                handling_employee_id, source, status, attribution_status, expires_at
            )
            VALUES ($1, $2, $3, 'EMPLOYEE', $4, $5, 'EMPLOYEE_SITE_VISIT', 'ACTIVE', 'VERIFIED', $6)
            RETURNING id, lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
                      source_owner_channel_partner_id, handling_employee_id, source, status,
                      attribution_status, locked_at, expires_at
            """,
            lead_id,
            project_id,
            property_id,
            source_owner_employee_id,
            handling_employee_id,
            expires_at,
        )
        return dict(row)

    async def renew(self, opportunity_id: str, expires_at: datetime, connection: Any) -> None:
        await connection.execute(
            "UPDATE opportunities SET expires_at = $2 WHERE id = $1", opportunity_id, expires_at
        )

    async def set_handling_employee(self, opportunity_id: str, handling_employee_id: str, connection: Any) -> None:
        await connection.execute(
            "UPDATE opportunities SET handling_employee_id = $2 WHERE id = $1 AND handling_employee_id IS NULL",
            opportunity_id,
            handling_employee_id,
        )

    async def mark_converted(self, opportunity_id: str, connection: Any) -> bool:
        """ACTIVE -> CONVERTED; False if the opportunity is no longer ACTIVE or its protection has lapsed."""
        updated_id = await connection.fetchval(
            """
            UPDATE opportunities SET status = 'CONVERTED'
            WHERE id = $1 AND status = 'ACTIVE' AND expires_at > now()
            RETURNING id
            """,
            opportunity_id,
        )
        return updated_id is not None

    async def mark_attribution_conflict(self, opportunity_id: str, connection: Any) -> None:
        await connection.execute(
            "UPDATE opportunities SET status = 'ATTRIBUTION_CONFLICT', attribution_status = 'CONFLICT' WHERE id = $1",
            opportunity_id,
        )

    async def resolve_conflict(
        self,
        opportunity_id: str,
        resolved_source_owner_type: str,
        resolved_source_owner_employee_id: str | None,
        resolved_source_owner_channel_partner_id: str | None,
        reason: str,
        resolved_by: str,
        connection: Any,
    ) -> dict[str, Any] | None:
        """Returns None (and writes nothing) unless the opportunity is still in ATTRIBUTION_CONFLICT."""
        updated_id = await connection.fetchval(
            """
            UPDATE opportunities
            SET status = 'ACTIVE', attribution_status = 'RESOLVED',
                source_owner_type = $2, source_owner_employee_id = $3, source_owner_channel_partner_id = $4
            WHERE id = $1 AND status = 'ATTRIBUTION_CONFLICT'
            RETURNING id
            """,
            opportunity_id,
            resolved_source_owner_type,
            resolved_source_owner_employee_id,
            resolved_source_owner_channel_partner_id,
        )
        if updated_id is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO opportunity_resolutions (
                opportunity_id, resolved_source_owner_type, resolved_source_owner_employee_id,
                resolved_source_owner_channel_partner_id, reason, resolved_by
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, opportunity_id, resolved_source_owner_type, resolved_source_owner_employee_id,
                      resolved_source_owner_channel_partner_id, reason, resolved_by, resolved_at
            """,
            opportunity_id,
            resolved_source_owner_type,
            resolved_source_owner_employee_id,
            resolved_source_owner_channel_partner_id,
            reason,
            resolved_by,
        )
        return dict(row)

    async def create_claim(
        self,
        opportunity_id: str,
        claimant_employee_id: str,
        claim_type: str,
        evidence_site_visit_id: str | None,
        qualifying_at: datetime,
        connection: Any,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO opportunity_claims (
                opportunity_id, claimant_type, claimant_employee_id, claim_type,
                evidence_site_visit_id, qualifying_at
            )
            VALUES ($1, 'EMPLOYEE', $2, $3, $4, $5)
            """,
            opportunity_id,
            claimant_employee_id,
            claim_type,
            evidence_site_visit_id,
            qualifying_at,
        )

    async def expire_stale_opportunities(self) -> int:
        """§16.10 EXPIRED: protection period ended without qualifying activity."""
        async with self._db.acquire() as conn:
            result = await conn.execute(
                "UPDATE opportunities SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND expires_at <= now()"
            )
        parts = result.split()
        return int(parts[-1]) if parts and parts[-1].isdigit() else 0

    async def get_by_id(self, opportunity_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
                       source_owner_channel_partner_id, handling_employee_id, source, status,
                       attribution_status, locked_at, expires_at, created_at, updated_at
                FROM opportunities
                WHERE id = $1
                """,
                opportunity_id,
            )
        return dict(row) if row else None

    async def list_for_employee(self, employee_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, lead_id, project_id, property_id, source_owner_type, source_owner_employee_id,
                       source_owner_channel_partner_id, handling_employee_id, source, status,
                       attribution_status, locked_at, expires_at, created_at, updated_at
                FROM opportunities
                WHERE source_owner_employee_id = $1 OR handling_employee_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                employee_id,
                page_size,
                offset,
            )
            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM opportunities
                WHERE source_owner_employee_id = $1 OR handling_employee_id = $1
                """,
                employee_id,
            )
        return [dict(row) for row in rows], total

    async def list_claims(self, opportunity_id: str) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, opportunity_id, claimant_type, claimant_employee_id, claimant_channel_partner_id,
                       claim_type, evidence_site_visit_id, evidence_reference, qualifying_at, submitted_at
                FROM opportunity_claims
                WHERE opportunity_id = $1
                ORDER BY qualifying_at ASC
                """,
                opportunity_id,
            )
        return [dict(row) for row in rows]
