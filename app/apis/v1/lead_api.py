"""Leads API — /api/v1/leads/*."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import BusinessClockDep, CurrentEmployeeDep, DatabaseDep, SettingsDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, PaginationMeta, SuccessResponse
from app.persistence.follow_up_persistence import FollowUpPersistence
from app.persistence.lead_persistence import LeadPersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.schemas.lead_schema import ActiveLockResponse, FollowUpActionResponse, LeadResponse, LogFollowUpRequest
from app.service.lead_service import LeadService

logger = logging.getLogger("divine_vision.lead_api")

router = APIRouter(prefix="/leads", tags=["Leads"], dependencies=[Depends(enforce_rate_limit)])


def get_lead_service(db: DatabaseDep, business_clock: BusinessClockDep, settings: SettingsDep) -> LeadService:
    return LeadService(
        db,
        LeadPersistence(db),
        LockPersistence(db),
        FollowUpPersistence(db),
        OpportunityPersistence(db),
        business_clock,
        settings,
    )


LeadServiceDep = Annotated[LeadService, Depends(get_lead_service)]


@router.get("", response_model=PaginatedResponse[LeadResponse])
async def search_leads(
    current_employee: CurrentEmployeeDep,
    lead_service: LeadServiceDep,
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[LeadResponse]:
    try:
        items, total = await lead_service.search(current_employee.employee_id, q, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error searching leads")
        raise


@router.get("/active-locks", response_model=SuccessResponse[list[ActiveLockResponse]])
async def list_active_locks(
    current_employee: CurrentEmployeeDep,
    lead_service: LeadServiceDep,
) -> SuccessResponse[list[ActiveLockResponse]]:
    try:
        result = await lead_service.list_active_locks(current_employee.employee_id)
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing active locks")
        raise


@router.get("/{lead_id}", response_model=SuccessResponse[LeadResponse])
async def get_lead(
    lead_id: UUID,
    current_employee: CurrentEmployeeDep,
    lead_service: LeadServiceDep,
) -> SuccessResponse[LeadResponse]:
    try:
        result = await lead_service.get_lead(current_employee.employee_id, str(lead_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching lead")
        raise


@router.post("/{lead_id}/follow-ups", response_model=SuccessResponse[FollowUpActionResponse])
async def log_follow_up(
    lead_id: UUID,
    payload: LogFollowUpRequest,
    current_employee: CurrentEmployeeDep,
    lead_service: LeadServiceDep,
) -> SuccessResponse[FollowUpActionResponse]:
    try:
        result = await lead_service.log_follow_up(
            current_employee.employee_id, str(lead_id), payload.action_type, payload.notes, payload.reference
        )
        return SuccessResponse(data=result, message="Follow-up logged")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error logging follow-up")
        raise


@router.get("/{lead_id}/follow-ups", response_model=SuccessResponse[list[FollowUpActionResponse]])
async def list_follow_ups(
    lead_id: UUID,
    current_employee: CurrentEmployeeDep,
    lead_service: LeadServiceDep,
) -> SuccessResponse[list[FollowUpActionResponse]]:
    try:
        result = await lead_service.list_follow_ups(current_employee.employee_id, str(lead_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing follow-ups")
        raise
