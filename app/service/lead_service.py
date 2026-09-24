"""Business logic for Leads, follow-ups and the active-lock view (requirement §5, §9)."""

from datetime import timedelta

from app.core.config import Settings
from app.core.datetime_utils import BusinessClock
from app.core.exceptions import ForbiddenError, NotFoundError
from app.persistence.db_persistence import Database
from app.persistence.follow_up_persistence import FollowUpPersistence
from app.persistence.lead_persistence import LeadPersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.schemas.lead_schema import ActiveLockResponse, FollowUpActionResponse, LeadResponse, OpportunityInLeadResponse

_QUALIFYING_ACTION_TYPES = {"CALL_LOGGED", "NEXT_VISIT_SCHEDULED", "PROPOSAL_SENT"}


class LeadService:
    def __init__(
        self,
        db: Database,
        lead_persistence: LeadPersistence,
        lock_persistence: LockPersistence,
        follow_up_persistence: FollowUpPersistence,
        opportunity_persistence: OpportunityPersistence,
        business_clock: BusinessClock,
        settings: Settings,
    ) -> None:
        self._db = db
        self._lead_persistence = lead_persistence
        self._lock_persistence = lock_persistence
        self._follow_up_persistence = follow_up_persistence
        self._opportunity_persistence = opportunity_persistence
        self._business_clock = business_clock
        self._settings = settings

    async def search(self, employee_id: str, query_text: str | None, page: int, page_size: int) -> tuple[list[LeadResponse], int]:
        rows, total = await self._lead_persistence.search(employee_id, query_text, page, page_size)
        # Search returns leads without opportunities for performance. Load opportunities only in detail view.
        return [self._to_response(row, []) for row in rows], total

    async def get_lead(self, employee_id: str, lead_id: str) -> LeadResponse:
        row = await self._lead_persistence.get_by_id(lead_id)
        if row is None:
            raise NotFoundError("Lead not found")
        self._assert_can_view_lead(row, employee_id)
        opportunities = await self._opportunity_persistence.list_for_lead(lead_id)
        return self._to_response(row, opportunities)

    async def log_follow_up(
        self, employee_id: str, lead_id: str, action_type: str, notes: str | None, reference: str | None
    ) -> FollowUpActionResponse:
        lead = await self._lead_persistence.get_by_id(lead_id)
        if lead is None:
            raise NotFoundError("Lead not found")

        async with self._db.transaction() as conn:
            active_lock = await self._lock_persistence.get_active_lead_lock(lead_id, connection=conn)
            if active_lock is None or active_lock["employee_id"] != employee_id:
                raise ForbiddenError("You do not hold an active lock on this lead")

            follow_up = await self._follow_up_persistence.create(
                lead_id, employee_id, action_type, notes, reference, connection=conn
            )

            if action_type in _QUALIFYING_ACTION_TYPES:
                new_expires_at = self._business_clock.now() + timedelta(
                    days=self._settings.lead_property_lock_duration_days
                )
                await self._lock_persistence.renew_lead_lock(str(active_lock["id"]), new_expires_at, connection=conn)
                await self._lock_persistence.renew_property_locks_for_lead_lock(
                    str(active_lock["id"]), new_expires_at, connection=conn
                )

        return FollowUpActionResponse(
            id=str(follow_up["id"]),
            lead_id=str(follow_up["lead_id"]),
            employee_id=str(follow_up["employee_id"]),
            action_type=follow_up["action_type"],
            notes=follow_up["notes"],
            reference=follow_up["reference"],
            logged_at=follow_up["logged_at"],
        )

    async def list_follow_ups(self, employee_id: str, lead_id: str) -> list[FollowUpActionResponse]:
        lead = await self._lead_persistence.get_by_id(lead_id)
        if lead is None:
            raise NotFoundError("Lead not found")
        self._assert_can_view_lead(lead, employee_id)

        rows = await self._follow_up_persistence.list_for_lead(lead_id)
        return [
            FollowUpActionResponse(
                id=str(row["id"]),
                lead_id=str(row["lead_id"]),
                employee_id=str(row["employee_id"]),
                action_type=row["action_type"],
                notes=row["notes"],
                reference=row["reference"],
                logged_at=row["logged_at"],
            )
            for row in rows
        ]

    async def list_active_locks(self, employee_id: str) -> list[ActiveLockResponse]:
        rows = await self._lock_persistence.list_active_locks_for_employee(employee_id)
        return [
            ActiveLockResponse(
                lead_lock_id=str(row["lead_lock_id"]),
                lead_id=str(row["lead_id"]),
                lead_name=row["lead_name"],
                expires_at=row["expires_at"],
                property_id=str(row["property_id"]) if row["property_id"] else None,
                plot_no=row["plot_no"],
                project_name=row["project_name"],
            )
            for row in rows
        ]

    def _assert_can_view_lead(self, lead: dict, employee_id: str) -> None:
        """Matches the scoping `search()` already applies ("your own leads only") —
        viewing a single lead by id must not bypass that same boundary."""
        current_employee_id = str(lead["current_employee_id"]) if lead["current_employee_id"] else None
        if current_employee_id != employee_id:
            raise ForbiddenError("You do not have access to this lead")

    def _to_response(self, row: dict, opportunities: list[dict] | None = None) -> LeadResponse:
        if opportunities is None:
            opportunities = []
        opportunity_responses = [
            OpportunityInLeadResponse(
                id=str(opp["id"]),
                project_id=str(opp["project_id"]),
                property_id=str(opp["property_id"]) if opp["property_id"] else None,
                source_owner_type=opp["source_owner_type"],
                source_owner_employee_id=str(opp["source_owner_employee_id"]) if opp["source_owner_employee_id"] else None,
                source_owner_channel_partner_id=str(opp["source_owner_channel_partner_id"])
                if opp["source_owner_channel_partner_id"]
                else None,
                handling_employee_id=str(opp["handling_employee_id"]) if opp["handling_employee_id"] else None,
                source=opp["source"],
                status=opp["status"],
                attribution_status=opp["attribution_status"],
                locked_at=opp["locked_at"],
                expires_at=opp["expires_at"],
                created_at=opp["created_at"],
                updated_at=opp["updated_at"],
            )
            for opp in opportunities
        ]
        return LeadResponse(
            id=str(row["id"]),
            name=row["name"],
            normalized_phone=row["normalized_phone"],
            email=row["email"],
            source=row["source"],
            lifecycle_status=row["lifecycle_status"],
            first_visit_at=row["first_visit_at"],
            latest_visit_at=row["latest_visit_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            opportunities=opportunity_responses,
        )
