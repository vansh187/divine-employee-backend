"""Core business logic: Site Visit -> Lead resolution -> Lock engine -> Opportunity engine.

Requirement §3-5, §11: every step below executes inside ONE database transaction
so lead resolution, site-visit creation and lock creation are all-or-nothing,
and concurrent conflicting submissions are rejected by DB constraints, not just
application checks.
"""

import logging
from datetime import datetime, timedelta
from typing import NoReturn

import asyncpg

from app.core.config import Settings
from app.core.datetime_utils import BusinessClock
from app.core.exceptions import (
    ConflictError,
    DayOffConflictError,
    ForbiddenError,
    LeadLockedError,
    NotFoundError,
    OpportunityConflictError,
    PropertyLockedError,
    ValidationFailedError,
)
from app.core.phone_utils import PhoneNormalizer
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.db_persistence import Database
from app.persistence.lead_persistence import LeadPersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.property_persistence import PropertyPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.site_visit_schema import CreateSiteVisitRequest, SiteVisitResponse
from app.schemas.opportunity_schema import OPEN_STATUSES, OpportunityResponse
from app.service.notification_service import NotificationService
from app.service.opportunity_service import OpportunityService

logger = logging.getLogger("divine_vision.site_visit_service")


class SiteVisitService:
    def __init__(
        self,
        db: Database,
        site_visit_persistence: SiteVisitPersistence,
        lead_persistence: LeadPersistence,
        lock_persistence: LockPersistence,
        property_persistence: PropertyPersistence,
        day_off_persistence: DayOffPersistence,
        notification_service: NotificationService,
        opportunity_service: OpportunityService,
        phone_normalizer: PhoneNormalizer,
        business_clock: BusinessClock,
        settings: Settings,
    ) -> None:
        self._db = db
        self._site_visit_persistence = site_visit_persistence
        self._lead_persistence = lead_persistence
        self._lock_persistence = lock_persistence
        self._property_persistence = property_persistence
        self._day_off_persistence = day_off_persistence
        self._notification_service = notification_service
        self._opportunity_service = opportunity_service
        self._phone_normalizer = phone_normalizer
        self._business_clock = business_clock
        self._settings = settings

    async def create_site_visit(self, employee_id: str, payload: CreateSiteVisitRequest) -> SiteVisitResponse:
        if payload.idempotency_key:
            existing = await self._site_visit_persistence.find_by_idempotency_key(employee_id, payload.idempotency_key)
            if existing is not None:
                return await self.get_site_visit(employee_id, str(existing["id"]))

        normalized_phone = self._phone_normalizer.normalize(payload.phone)
        email = str(payload.email) if payload.email else None
        # Always timezone-aware: a form date+time sent without an offset is
        # business-local (IST). asyncpg would otherwise store a naive value as UTC.
        visit_at = self._business_clock.localize(payload.visit_at)
        # Day-off rules are per business day, regardless of the offset the client sent.
        visit_date = visit_at.date()

        project = await self._property_persistence.get_project_by_id(payload.project_id)
        if project is None:
            raise NotFoundError("Project not found")

        if payload.property_id:
            property_row = await self._property_persistence.get_property_by_id(payload.property_id)
            if property_row is None:
                raise NotFoundError("Property/plot not found")
            if str(property_row["project_id"]) != payload.project_id:
                raise ValidationFailedError("Selected plot does not belong to the selected project")
            if property_row["status"] in ("DEAL_LOCKED", "SOLD"):
                raise PropertyLockedError("This property is under a confirmed deal and is no longer available")

        is_frozen = await self._day_off_persistence.is_frozen_on_date(employee_id, visit_date)
        if is_frozen:
            raise DayOffConflictError()

        lock_expires_at = self._business_clock.now() + timedelta(days=self._settings.lead_property_lock_duration_days)

        try:
            async with self._db.transaction() as conn:
                lead = await self._resolve_or_create_lead(
                    conn, payload.visitor_name, normalized_phone, payload.phone, email,
                    employee_id, visit_at,
                )

                # Fetch each lock at most once per request — `FOR UPDATE` holds the
                # row lock for the whole transaction, so the conflict-check result
                # is still valid when we act on it after inserting the site visit.
                active_lead_lock = await self._lock_persistence.get_active_lead_lock(str(lead["id"]), connection=conn)
                if active_lead_lock is not None and active_lead_lock["employee_id"] != employee_id:
                    raise LeadLockedError()

                active_property_lock = None
                if payload.property_id:
                    active_property_lock = await self._lock_persistence.get_active_property_lock(
                        payload.property_id, connection=conn
                    )
                    if active_property_lock is not None and active_property_lock["employee_id"] != employee_id:
                        raise PropertyLockedError()

                site_visit = await self._site_visit_persistence.create(
                    employee_id=employee_id,
                    lead_id=str(lead["id"]),
                    visitor_name=payload.visitor_name,
                    visitor_phone=payload.phone,
                    visitor_email=email,
                    project_id=payload.project_id,
                    property_id=payload.property_id,
                    visit_at=visit_at,
                    notes=payload.notes,
                    attachments=payload.attachments,
                    outcome=payload.outcome,
                    idempotency_key=payload.idempotency_key,
                    connection=conn,
                )

                lead_lock = await self._create_or_renew_lead_lock(
                    conn, lead, employee_id, str(site_visit["id"]), lock_expires_at, active_lead_lock
                )

                if payload.property_id:
                    await self._create_or_renew_property_lock(
                        conn, payload.property_id, employee_id, str(site_visit["id"]), str(lead_lock["id"]),
                        lock_expires_at, active_property_lock,
                    )

                await self._lead_persistence.touch_latest_visit(
                    str(lead["id"]), employee_id, visit_at, email=email, connection=conn
                )

                await self._opportunity_service.record_employee_claim(
                    connection=conn,
                    lead_id=str(lead["id"]),
                    project_id=payload.project_id,
                    property_id=payload.property_id,
                    employee_id=employee_id,
                    site_visit_id=str(site_visit["id"]),
                    qualifying_at=visit_at,
                    expires_at=lock_expires_at,
                )

                await self._notification_service.emit(
                    employee_id=employee_id,
                    event_type="SITE_VISIT_LOGGED",
                    entity_type="SITE_VISIT",
                    entity_id=str(site_visit["id"]),
                    message=f"Site visit logged for {payload.visitor_name}"
                    + (f" · Plot {payload.property_id}" if payload.property_id else ""),
                    connection=conn,
                )
        except asyncpg.UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", "") or ""
            if "idempotency_key" in constraint and payload.idempotency_key:
                # A concurrent retry with the same key committed first — return its visit.
                existing = await self._site_visit_persistence.find_by_idempotency_key(
                    employee_id, payload.idempotency_key
                )
                if existing is not None:
                    return await self.get_site_visit(employee_id, str(existing["id"]))
            self._translate_unique_violation(exc)

        return await self.get_site_visit(employee_id, str(site_visit["id"]))

    async def get_site_visit(self, employee_id: str, site_visit_id: str) -> SiteVisitResponse:
        row = await self._site_visit_persistence.get_by_id(site_visit_id)
        if row is None:
            raise NotFoundError("Site visit not found")
        if str(row["employee_id"]) != employee_id:
            raise ForbiddenError("You do not have access to this site visit")
        return self._to_response(row)

    async def list_for_employee(self, employee_id: str, page: int, page_size: int) -> tuple[list[SiteVisitResponse], int]:
        rows, total = await self._site_visit_persistence.list_for_employee(employee_id, page, page_size)
        return [self._to_response(row) for row in rows], total

    async def _resolve_or_create_lead(
        self, conn, name: str, normalized_phone: str, raw_phone: str, email: str | None,
        employee_id: str, visit_at: datetime,
    ) -> dict:
        existing = await self._lead_persistence.get_by_normalized_phone(normalized_phone, connection=conn)
        if existing is not None:
            return existing
        return await self._lead_persistence.get_or_create(
            name=name, normalized_phone=normalized_phone, raw_phone=raw_phone, email=email,
            originating_employee_id=employee_id, visit_at=visit_at, connection=conn,
        )

    async def _create_or_renew_lead_lock(
        self, conn, lead: dict, employee_id: str, site_visit_id: str, expires_at: datetime, active_lock: dict | None
    ) -> dict:
        if active_lock is not None and active_lock["employee_id"] == employee_id:
            await self._lock_persistence.renew_lead_lock(str(active_lock["id"]), expires_at, connection=conn)
            active_lock["expires_at"] = expires_at
            return active_lock
        return await self._lock_persistence.create_lead_lock(
            str(lead["id"]), employee_id, site_visit_id, expires_at, connection=conn
        )

    async def _create_or_renew_property_lock(
        self, conn, property_id: str, employee_id: str, site_visit_id: str, lead_lock_id: str, expires_at: datetime,
        active_lock: dict | None,
    ) -> None:
        if active_lock is not None and active_lock["employee_id"] == employee_id:
            # Renew the plot's own lock by id — it may hang off a different lead's
            # lead lock than the one this visit is for.
            await self._lock_persistence.renew_property_lock(str(active_lock["id"]), expires_at, connection=conn)
            return
        await self._lock_persistence.create_property_lock(
            property_id, employee_id, site_visit_id, lead_lock_id, expires_at, connection=conn
        )
        # Only AVAILABLE -> LOCKED; a DEAL_LOCKED/SOLD plot must never be
        # silently downgraded by a new employee's site visit.
        property_row = await self._property_persistence.get_property_by_id(property_id, connection=conn)
        if property_row is not None and property_row["status"] == "AVAILABLE":
            await self._property_persistence.update_status(property_id, "LOCKED", connection=conn)

    def _translate_unique_violation(self, exc: asyncpg.UniqueViolationError) -> NoReturn:
        constraint = getattr(exc, "constraint_name", "") or ""
        if "lead_lock" in constraint:
            raise LeadLockedError() from exc
        if "property_lock" in constraint:
            raise PropertyLockedError() from exc
        if "opportunities" in constraint:
            raise OpportunityConflictError() from exc
        logger.warning("Unhandled unique violation: %s", constraint)
        raise ConflictError("CONCURRENT_UPDATE", "A conflicting request was processed at the same time; please retry") from exc

    def _to_response(self, row: dict) -> SiteVisitResponse:
        opportunity = (
            OpportunityResponse.model_validate(row["opportunity"])
            if row.get("opportunity") else None
        )
        can_update = bool(
            opportunity
            and str(row["employee_id"]) in (
                opportunity.source_owner_employee_id, opportunity.handling_employee_id
            )
            and opportunity.status in OPEN_STATUSES
            and opportunity.expires_at > self._business_clock.now()
        )
        return SiteVisitResponse(
            id=str(row["id"]),
            employee_id=str(row["employee_id"]),
            lead_id=str(row["lead_id"]),
            visitor_name=row.get("visitor_name"),
            visitor_phone=row.get("visitor_phone"),
            visitor_email=row.get("visitor_email"),
            project_id=str(row["project_id"]),
            property_id=str(row["property_id"]) if row.get("property_id") else None,
            visit_at=row["visit_at"],
            notes=row.get("notes"),
            attachments=list(row.get("attachments") or []),
            outcome=row.get("outcome"),
            lead_name=row.get("lead_name"),
            project_name=row.get("project_name"),
            plot_no=row.get("plot_no"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            opportunity=opportunity,
            can_update_opportunity=can_update,
        )
