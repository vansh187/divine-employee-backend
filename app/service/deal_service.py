"""Business logic for Opportunity -> Deal conversion and the hard Deal Lock (§20.6).

A DEAL_LOCKED plot blocks every other Opportunity/Visit/Deal until the deal is
completed (-> SOLD) or cancelled (-> AVAILABLE). Deal locks never auto-expire.
"""

import asyncpg

from app.core.exceptions import ConflictError, DealLockedError, ForbiddenError, NotFoundError, ValidationFailedError
from app.persistence.db_persistence import Database
from app.persistence.deal_persistence import DealPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.persistence.property_persistence import PropertyPersistence
from app.schemas.deal_schema import DealResponse


class DealService:
    def __init__(
        self,
        db: Database,
        deal_persistence: DealPersistence,
        opportunity_persistence: OpportunityPersistence,
        property_persistence: PropertyPersistence,
    ) -> None:
        self._db = db
        self._deal_persistence = deal_persistence
        self._opportunity_persistence = opportunity_persistence
        self._property_persistence = property_persistence

    async def create_deal(self, employee_id: str, opportunity_id: str) -> DealResponse:
        opportunity = await self._opportunity_persistence.get_by_id(opportunity_id)
        if opportunity is None:
            raise NotFoundError("Opportunity not found")
        # Same rule as _assert_owns_deal: only the source owner or handling
        # employee may convert (and thereby deal-lock the plot).
        if employee_id not in (opportunity["source_owner_employee_id"], opportunity["handling_employee_id"]):
            raise ForbiddenError("You are not the source owner or handling employee for this opportunity")
        if opportunity["status"] != "ACTIVE":
            raise ConflictError("OPPORTUNITY_NOT_ACTIVE", "Only an ACTIVE opportunity can be converted to a deal")
        if not opportunity["property_id"]:
            raise ValidationFailedError("A deal requires an opportunity tied to a specific property/plot")

        property_id = str(opportunity["property_id"])

        try:
            async with self._db.transaction() as conn:
                # Claim the opportunity first: a concurrent conversion (or an
                # expiry/conflict since the read above) makes this a no-op.
                if not await self._opportunity_persistence.mark_converted(opportunity_id, connection=conn):
                    raise ConflictError("OPPORTUNITY_NOT_ACTIVE", "Only an ACTIVE opportunity can be converted to a deal")

                existing_lock = await self._deal_persistence.get_active_lock_for_property(property_id, connection=conn)
                if existing_lock is not None:
                    raise DealLockedError()

                deal = await self._deal_persistence.create_deal(
                    opportunity_id=opportunity_id,
                    lead_id=str(opportunity["lead_id"]),
                    project_id=str(opportunity["project_id"]),
                    property_id=property_id,
                    source_owner_type=opportunity["source_owner_type"],
                    source_owner_employee_id=str(opportunity["source_owner_employee_id"])
                    if opportunity["source_owner_employee_id"]
                    else None,
                    source_owner_channel_partner_id=str(opportunity["source_owner_channel_partner_id"])
                    if opportunity["source_owner_channel_partner_id"]
                    else None,
                    handling_employee_id=str(opportunity["handling_employee_id"])
                    if opportunity["handling_employee_id"]
                    else None,
                    connection=conn,
                )
                await self._deal_persistence.create_deal_lock(str(deal["id"]), property_id, connection=conn)
                await self._property_persistence.update_status(property_id, "DEAL_LOCKED", connection=conn)
        except asyncpg.UniqueViolationError as exc:
            raise DealLockedError() from exc

        return self._to_response(deal)

    async def complete_deal(self, employee_id: str, deal_id: str) -> DealResponse:
        deal = await self._deal_persistence.get_by_id(deal_id)
        if deal is None:
            raise NotFoundError("Deal not found")
        self._assert_owns_deal(deal, employee_id)

        async with self._db.transaction() as conn:
            updated = await self._deal_persistence.update_deal_status(
                deal_id, "COMPLETED", connection=conn, expected_current_statuses=["CONFIRMED"]
            )
            if updated is None:
                raise ConflictError(
                    "DEAL_NOT_CONFIRMED", f"Deal is '{deal['status']}', not CONFIRMED — cannot complete it"
                )
            await self._property_persistence.update_status(str(deal["property_id"]), "SOLD", connection=conn)

        return self._to_response(updated)

    async def cancel_deal(self, employee_id: str, deal_id: str) -> DealResponse:
        deal = await self._deal_persistence.get_by_id(deal_id)
        if deal is None:
            raise NotFoundError("Deal not found")
        self._assert_owns_deal(deal, employee_id)

        async with self._db.transaction() as conn:
            updated = await self._deal_persistence.update_deal_status(
                deal_id, "CANCELLED", connection=conn, expected_current_statuses=["CONFIRMED"]
            )
            if updated is None:
                raise ConflictError(
                    "DEAL_NOT_CONFIRMED", f"Deal is '{deal['status']}', not CONFIRMED — cannot cancel it"
                )
            await self._deal_persistence.release_deal_lock(deal_id, connection=conn)
            await self._property_persistence.update_status(str(deal["property_id"]), "AVAILABLE", connection=conn)

        return self._to_response(updated)

    async def get_deal(self, employee_id: str, deal_id: str) -> DealResponse:
        deal = await self._deal_persistence.get_by_id(deal_id)
        if deal is None:
            raise NotFoundError("Deal not found")
        self._assert_owns_deal(deal, employee_id)
        return self._to_response(deal)

    def _assert_owns_deal(self, deal: dict, employee_id: str) -> None:
        source_owner_employee_id = str(deal["source_owner_employee_id"]) if deal["source_owner_employee_id"] else None
        handling_employee_id = str(deal["handling_employee_id"]) if deal["handling_employee_id"] else None
        if employee_id not in (source_owner_employee_id, handling_employee_id):
            raise ForbiddenError("You are not the source owner or handling employee for this deal")

    async def list_for_employee(self, employee_id: str, page: int, page_size: int) -> tuple[list[DealResponse], int]:
        rows, total = await self._deal_persistence.list_for_employee(employee_id, page, page_size)
        return [self._to_response(row) for row in rows], total

    def _to_response(self, row: dict) -> DealResponse:
        return DealResponse(
            id=str(row["id"]),
            opportunity_id=str(row["opportunity_id"]),
            lead_id=str(row["lead_id"]),
            project_id=str(row["project_id"]),
            property_id=str(row["property_id"]),
            source_owner_type=row["source_owner_type"],
            source_owner_employee_id=str(row["source_owner_employee_id"]) if row["source_owner_employee_id"] else None,
            source_owner_channel_partner_id=str(row["source_owner_channel_partner_id"])
            if row["source_owner_channel_partner_id"]
            else None,
            handling_employee_id=str(row["handling_employee_id"]) if row["handling_employee_id"] else None,
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
