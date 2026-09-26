"""All SQL for `plot_waitlist` — leads waiting for a plot another employee currently holds."""

from typing import Any

from app.persistence.db_persistence import Database


class WaitlistPersistence:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self, property_id: str, lead_id: str, employee_id: str, site_visit_id: str, connection: Any
    ) -> None:
        """Idempotent: a lead already waiting for the plot keeps its place in the queue."""
        await connection.execute(
            """
            INSERT INTO plot_waitlist (property_id, lead_id, employee_id, site_visit_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (property_id, lead_id) WHERE status = 'WAITING' DO NOTHING
            """,
            property_id,
            lead_id,
            employee_id,
            site_visit_id,
        )

    async def cancel_for_employee(self, property_id: str, employee_id: str, connection: Any) -> None:
        """The employee now holds the plot themselves, so their waiting entries are moot."""
        await connection.execute(
            """
            UPDATE plot_waitlist SET status = 'CANCELLED'
            WHERE property_id = $1 AND employee_id = $2 AND status = 'WAITING'
            """,
            property_id,
            employee_id,
        )

    async def promote_next_for_available_plots(self, property_ids: list[str], connection: Any) -> int:
        """For each of these plots that is now AVAILABLE, mark the oldest waiting entry PROMOTED
        and notify its employee. The employee takes the plot by logging a new site visit."""
        if not property_ids:
            return 0
        # Entries whose lead lock lapsed (or whose lead moved to someone else) are stale: cancel them so
        # they never stall the queue, then promote the oldest entry that is still valid.
        await connection.execute(
            """
            UPDATE plot_waitlist w SET status = 'CANCELLED'
            WHERE w.status = 'WAITING' AND w.property_id = ANY($1::uuid[])
              AND NOT EXISTS (
                  SELECT 1 FROM lead_locks ll
                  WHERE ll.lead_id = w.lead_id AND ll.employee_id = w.employee_id
                    AND ll.status = 'ACTIVE' AND ll.expires_at > now()
              )
            """,
            property_ids,
        )
        promoted = await connection.fetch(
            """
            WITH next AS (
                SELECT DISTINCT ON (w.property_id) w.id
                FROM plot_waitlist w
                JOIN properties p ON p.id = w.property_id
                WHERE w.status = 'WAITING' AND w.property_id = ANY($1::uuid[]) AND p.status = 'AVAILABLE'
                ORDER BY w.property_id, w.created_at
            ),
            updated AS (
                UPDATE plot_waitlist w SET status = 'PROMOTED'
                FROM next WHERE w.id = next.id
                RETURNING w.employee_id, w.property_id
            )
            INSERT INTO notifications (employee_id, event_type, entity_type, entity_id, message)
            SELECT employee_id, 'PROPERTY_LOCK_RELEASED', 'PROPERTY', property_id,
                   'A plot you were waiting for is available again. Log a site visit to reserve it.'
            FROM updated
            RETURNING id
            """,
            property_ids,
        )
        return len(promoted)
