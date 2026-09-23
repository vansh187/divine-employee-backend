"""Site Visit API — /api/v1/site-visits/* (requirement §3-5)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings
from app.core.datetime_utils import BusinessClock
from app.core.deps import BusinessClockDep, CurrentEmployeeDep, DatabaseDep, SettingsDep
from app.core.exceptions import AppError
from app.core.phone_utils import PhoneNormalizer
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, PaginationMeta, SuccessResponse
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.db_persistence import Database
from app.persistence.lead_persistence import LeadPersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.notification_persistence import NotificationPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.persistence.property_persistence import PropertyPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.site_visit_schema import CreateSiteVisitRequest, SiteVisitResponse
from app.service.notification_service import NotificationService
from app.service.opportunity_service import OpportunityService
from app.service.site_visit_service import SiteVisitService

logger = logging.getLogger("divine_vision.site_visit_api")

router = APIRouter(prefix="/site-visits", tags=["Site Visits"], dependencies=[Depends(enforce_rate_limit)])


def get_site_visit_service(db: DatabaseDep, business_clock: BusinessClockDep, settings: SettingsDep) -> SiteVisitService:
    notification_service = NotificationService(NotificationPersistence(db))
    opportunity_service = OpportunityService(db, OpportunityPersistence(db), notification_service, settings)
    return SiteVisitService(
        db=db,
        site_visit_persistence=SiteVisitPersistence(db),
        lead_persistence=LeadPersistence(db),
        lock_persistence=LockPersistence(db),
        property_persistence=PropertyPersistence(db),
        day_off_persistence=DayOffPersistence(db),
        notification_service=notification_service,
        opportunity_service=opportunity_service,
        phone_normalizer=PhoneNormalizer(),
        business_clock=business_clock,
        settings=settings,
    )


SiteVisitServiceDep = Annotated[SiteVisitService, Depends(get_site_visit_service)]


@router.post("", response_model=SuccessResponse[SiteVisitResponse])
async def create_site_visit(
    payload: CreateSiteVisitRequest,
    current_employee: CurrentEmployeeDep,
    site_visit_service: SiteVisitServiceDep,
) -> SuccessResponse[SiteVisitResponse]:
    try:
        result = await site_visit_service.create_site_visit(current_employee.employee_id, payload)
        return SuccessResponse(data=result, message="Site visit logged")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error creating site visit")
        raise


@router.get("/{site_visit_id}", response_model=SuccessResponse[SiteVisitResponse])
async def get_site_visit(
    site_visit_id: UUID,
    current_employee: CurrentEmployeeDep,
    site_visit_service: SiteVisitServiceDep,
) -> SuccessResponse[SiteVisitResponse]:
    try:
        result = await site_visit_service.get_site_visit(current_employee.employee_id, str(site_visit_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching site visit")
        raise


@router.get("", response_model=PaginatedResponse[SiteVisitResponse])
async def list_site_visits(
    current_employee: CurrentEmployeeDep,
    site_visit_service: SiteVisitServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[SiteVisitResponse]:
    try:
        items, total = await site_visit_service.list_for_employee(current_employee.employee_id, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing site visits")
        raise
