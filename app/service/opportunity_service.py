"""REQ-25 — Unified Opportunity & Attribution Engine.

Single centralized conflict service shared by every channel that can create an
opportunity (today: Employee Site Visits; future: Channel Partner submissions
must call this same service — §16.11: "separate lock engines are prohibited").

ASSUMED DEFAULTS for the still-open client decisions (OP-D01..OP-D05), applied
until Divine Vision confirms them — see db/001_init_schema.sql header comments
and requirement §13/§16.13 for the open-decision list:
  OP-D01: priority = earliest qualifying activity wins; ambiguous competing
          claims from two live parties enter ATTRIBUTION_CONFLICT rather than
          being auto-resolved.
  OP-D02: the 3-day lock is enforced at TWO independent layers — the V1 global
          PropertyLock (blocks any other lead) and this Opportunity (blocks
          attribution disputes for the same lead+plot). Both are always active.
  OP-D03: a Lead MAY hold simultaneous ACTIVE Opportunities on different plots.
  OP-D04: conflict resolution is exposed as a back-office-only endpoint,
          authenticated by a shared secret (not an employee JWT), since the
          Employee Portal has no Manager/Admin role.
  OP-D05: an unresolved ATTRIBUTION_CONFLICT blocks only that Lead+Plot pair,
          not the property globally (global blocking is the separate V1
          PropertyLock, unaffected by opportunity conflict state).
"""

import logging
from datetime import datetime

import asyncpg

from app.core.config import Settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.persistence.db_persistence import Database
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.schemas.opportunity_schema import OPEN_STATUSES, STAGE_RANK, OpportunityClaimResponse, OpportunityResponse
from app.persistence.deal_persistence import DealPersistence
from app.persistence.lead_persistence import LeadPersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.property_persistence import PropertyPersistence
from app.service.deal_service import DealService
from app.service.notification_service import NotificationService

logger = logging.getLogger("divine_vision.opportunity_service")


class OpportunityService:
    def __init__(
        self,
        db: Database,
        opportunity_persistence: OpportunityPersistence,
        notification_service: NotificationService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._opportunity_persistence = opportunity_persistence
        self._notification_service = notification_service
        self._settings = settings
        self._lock_persistence = LockPersistence(db)
        self._lead_persistence = LeadPersistence(db)
        self._deal_service = DealService(db, DealPersistence(db), opportunity_persistence, PropertyPersistence(db))

    async def record_employee_claim(
        self,
        connection,
        lead_id: str,
        project_id: str,
        property_id: str | None,
        employee_id: str,
        site_visit_id: str,
        qualifying_at: datetime,
        expires_at: datetime,
    ) -> dict:
        """Executes the unified conflict check (§16.6) inside the caller's transaction.

        `expires_at` is computed from server time by the caller (same as the V1
        locks), never from the client-supplied `qualifying_at` — otherwise a
        backdated or future-dated visit would shorten or stretch protection.
        """

        if property_id:
            opportunity = await self._opportunity_persistence.get_active_by_lead_and_property(
                lead_id, property_id, connection
            )
        else:
            opportunity = await self._opportunity_persistence.get_active_by_lead_and_project_no_property(
                lead_id, project_id, connection
            )

        if opportunity is None:
            opportunity = await self._opportunity_persistence.create(
                lead_id=lead_id,
                project_id=project_id,
                property_id=property_id,
                source_owner_employee_id=employee_id,
                handling_employee_id=employee_id,
                expires_at=expires_at,
                connection=connection,
            )
            await self._opportunity_persistence.create_claim(
                opportunity_id=str(opportunity["id"]),
                claimant_employee_id=employee_id,
                claim_type="SITE_VISIT",
                evidence_site_visit_id=site_visit_id,
                qualifying_at=qualifying_at,
                connection=connection,
            )
            return opportunity

        is_same_employee_owner = (
            opportunity["source_owner_type"] == "EMPLOYEE"
            and str(opportunity["source_owner_employee_id"]) == employee_id
        )
        is_channel_partner_owned = opportunity["source_owner_type"] == "CHANNEL_PARTNER"

        await self._opportunity_persistence.create_claim(
            opportunity_id=str(opportunity["id"]),
            claimant_employee_id=employee_id,
            claim_type="SITE_VISIT",
            evidence_site_visit_id=site_visit_id,
            qualifying_at=qualifying_at,
            connection=connection,
        )

        if is_same_employee_owner:
            await self._opportunity_persistence.renew(str(opportunity["id"]), expires_at, connection=connection)
            return opportunity

        if is_channel_partner_owned:
            # §16.5: employee conducting a Channel Partner's visit becomes Handling
            # Employee without transferring source ownership.
            await self._opportunity_persistence.set_handling_employee(
                str(opportunity["id"]), employee_id, connection=connection
            )
            return opportunity

        # Different employee already owns this Opportunity: a genuine competing
        # claim. Automatic attribution cannot be safely determined for two live
        # employees, so freeze the Opportunity into ATTRIBUTION_CONFLICT (§16.7)
        # rather than creating a second Opportunity or silently reassigning it.
        if opportunity["status"] != "ATTRIBUTION_CONFLICT":
            await self._opportunity_persistence.mark_attribution_conflict(str(opportunity["id"]), connection=connection)
            await self._notification_service.emit(
                employee_id=employee_id,
                event_type="OPPORTUNITY_CONFLICT",
                entity_type="OPPORTUNITY",
                entity_id=str(opportunity["id"]),
                message="Your claim on this lead/plot conflicts with an existing opportunity owner",
                connection=connection,
            )
        return opportunity

    async def get_opportunity(self, employee_id: str, opportunity_id: str) -> OpportunityResponse:
        row = await self._get_viewable_opportunity(employee_id, opportunity_id)
        return self._to_response(row)

    async def list_for_employee(self, employee_id: str, page: int, page_size: int) -> tuple[list[OpportunityResponse], int]:
        rows, total = await self._opportunity_persistence.list_for_employee(employee_id, page, page_size)
        return [self._to_response(row) for row in rows], total

    async def list_claims(self, employee_id: str, opportunity_id: str) -> list[OpportunityClaimResponse]:
        await self._get_viewable_opportunity(employee_id, opportunity_id)
        rows = await self._opportunity_persistence.list_claims(opportunity_id)
        return [
            OpportunityClaimResponse(
                id=str(row["id"]),
                claimant_type=row["claimant_type"],
                claimant_employee_id=str(row["claimant_employee_id"]) if row["claimant_employee_id"] else None,
                claimant_channel_partner_id=str(row["claimant_channel_partner_id"])
                if row["claimant_channel_partner_id"]
                else None,
                claim_type=row["claim_type"],
                evidence_site_visit_id=str(row["evidence_site_visit_id"]) if row["evidence_site_visit_id"] else None,
                qualifying_at=row["qualifying_at"],
                submitted_at=row["submitted_at"],
            )
            for row in rows
        ]

    async def update_status(self, employee_id: str, opportunity_id: str, new_status: str) -> OpportunityResponse:
        """Employee-driven status change for an open opportunity (NEW, INTERESTED, DEAL_IN_PROGRESS or ACTIVE).

        CONVERTED on a plot-tied opportunity runs the normal deal conversion (deal + plot lock);
        a plot-less one has no deal to create, so only its status changes.
        """
        row = await self._get_viewable_opportunity(employee_id, opportunity_id)
        if row["status"] not in OPEN_STATUSES:
            raise ConflictError(
                "OPPORTUNITY_NOT_ACTIVE", f"Opportunity is '{row['status']}'; only an open opportunity can be updated"
            )

        if new_status == row["status"]:
            # Already in the requested state (e.g. INTERESTED again): nothing to change or notify.
            return self._to_response(row)

        if new_status in STAGE_RANK and STAGE_RANK[new_status] < STAGE_RANK[row["status"]]:
            raise ConflictError(
                "INVALID_STATUS_TRANSITION", f"Cannot move an opportunity from '{row['status']}' back to '{new_status}'"
            )

        deal_created = new_status == "CONVERTED" and row["property_id"] is not None
        if deal_created:
            await self._deal_service.create_deal(employee_id, opportunity_id)
        else:
            async with self._db.transaction() as conn:
                if not await self._opportunity_persistence.set_status_if_active(
                    opportunity_id, new_status, connection=conn
                ):
                    raise ConflictError(
                        "OPPORTUNITY_NOT_ACTIVE", "Only an open, unexpired opportunity can be updated"
                    )
                if new_status in ("LOST", "RELEASED", "DEAL_REJECTED") and row["property_id"] is not None:
                    await self._lock_persistence.release_property_lock_for_lead(
                        str(row["lead_id"]), str(row["property_id"]), connection=conn
                    )

        if new_status in ("INTERESTED", "DEAL_IN_PROGRESS", "CONVERTED"):
            # Interested onwards, the lead is treated as a customer.
            await self._mark_lead_converted(row)
        await self._notify_status_change(row, new_status, deal_created)
        updated = await self._opportunity_persistence.get_by_id(opportunity_id)
        if updated is None:
            raise NotFoundError("Opportunity not found")
        return self._to_response(updated)

    async def _mark_lead_converted(self, row: dict) -> None:
        # Best-effort: the opportunity is already converted; a failure here must not fail the request.
        try:
            await self._lead_persistence.mark_converted(str(row["lead_id"]))
        except Exception:
            logger.exception("Failed to mark lead %s converted", row["lead_id"])

    async def _notify_status_change(self, row: dict, new_status: str, deal_created: bool) -> None:
        # Best-effort: the status change is already committed, so a notification failure must not fail the request.
        recipients = {
            str(employee)
            for employee in (row["source_owner_employee_id"], row["handling_employee_id"])
            if employee
        }
        if new_status == "CONVERTED":
            event_type = "DEAL_LOCKED" if deal_created else "OPPORTUNITY_RESOLVED"
            message = "Deal done: opportunity marked as converted" + (" and plot deal-locked" if deal_created else "")
        else:
            event_type = "OPPORTUNITY_RESOLVED"
            message = f"Opportunity marked as {new_status.lower()}"
        for recipient in recipients:
            try:
                await self._notification_service.emit(
                    employee_id=recipient,
                    event_type=event_type,
                    entity_type="OPPORTUNITY",
                    entity_id=str(row["id"]),
                    message=message,
                )
            except Exception:
                logger.exception("Failed to emit opportunity status notification")

    async def resolve_conflict(
        self,
        opportunity_id: str,
        resolved_source_owner_type: str,
        resolved_source_owner_employee_id: str | None,
        resolved_source_owner_channel_partner_id: str | None,
        reason: str,
        resolved_by: str,
    ) -> OpportunityResponse:
        """AC-OPP-07: resolution is recorded immutably; prior claims are never deleted (§16.7, §18.7).

        Only an opportunity currently in ATTRIBUTION_CONFLICT can be resolved — a
        CONVERTED/EXPIRED/LOST one must never be revived to ACTIVE.
        """
        existing = await self._opportunity_persistence.get_by_id(opportunity_id)
        if existing is None:
            raise NotFoundError("Opportunity not found")
        if existing["status"] != "ATTRIBUTION_CONFLICT":
            raise ConflictError(
                "OPPORTUNITY_NOT_IN_CONFLICT",
                f"Opportunity is '{existing['status']}', not ATTRIBUTION_CONFLICT — nothing to resolve",
            )

        try:
            async with self._db.transaction() as conn:
                resolution = await self._opportunity_persistence.resolve_conflict(
                    opportunity_id=opportunity_id,
                    resolved_source_owner_type=resolved_source_owner_type,
                    resolved_source_owner_employee_id=resolved_source_owner_employee_id,
                    resolved_source_owner_channel_partner_id=resolved_source_owner_channel_partner_id,
                    reason=reason,
                    resolved_by=resolved_by,
                    connection=conn,
                )
                if resolution is None:
                    # Status changed between the read above and the conditional UPDATE.
                    raise ConflictError(
                        "OPPORTUNITY_NOT_IN_CONFLICT", "Opportunity is no longer in ATTRIBUTION_CONFLICT"
                    )
        except asyncpg.ForeignKeyViolationError as exc:
            raise NotFoundError("Resolved source owner does not exist") from exc

        row = await self._opportunity_persistence.get_by_id(opportunity_id)
        return self._to_response(row)

    async def _get_viewable_opportunity(self, employee_id: str, opportunity_id: str) -> dict:
        """Same scoping as `list_for_employee`: only the source owner or handling employee."""
        row = await self._opportunity_persistence.get_by_id(opportunity_id)
        if row is None:
            raise NotFoundError("Opportunity not found")
        if employee_id not in (row["source_owner_employee_id"], row["handling_employee_id"]):
            raise ForbiddenError("You do not have access to this opportunity")
        return row

    def _to_response(self, row: dict) -> OpportunityResponse:
        return OpportunityResponse(
            id=str(row["id"]),
            lead_id=str(row["lead_id"]),
            project_id=str(row["project_id"]),
            property_id=str(row["property_id"]) if row["property_id"] else None,
            source_owner_type=row["source_owner_type"],
            source_owner_employee_id=str(row["source_owner_employee_id"]) if row["source_owner_employee_id"] else None,
            source_owner_channel_partner_id=str(row["source_owner_channel_partner_id"])
            if row["source_owner_channel_partner_id"]
            else None,
            handling_employee_id=str(row["handling_employee_id"]) if row["handling_employee_id"] else None,
            source=row["source"],
            status=row["status"],
            attribution_status=row["attribution_status"],
            locked_at=row["locked_at"],
            expires_at=row["expires_at"],
            created_at=row.get("created_at") or row["locked_at"],
            updated_at=row.get("updated_at") or row["locked_at"],
        )
