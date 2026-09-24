"""Opportunity Engine API — /api/v1/opportunities/* (REQ-25).

Employee-facing endpoints are read-only (view own opportunities/claims); the
conflict-resolution endpoint is back-office-only (OP-D04 assumed default —
see app/service/opportunity_service.py header for all open-decision defaults).
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import BackOfficeAuthDep, CurrentEmployeeDep, DatabaseDep, SettingsDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, SuccessResponse
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.schemas.opportunity_schema import (
    OpportunityClaimResponse,
    OpportunityResponse,
    ResolveConflictRequest,
    UpdateOpportunityStatusRequest,
)
from app.service.notification_service import NotificationService
from app.persistence.notification_persistence import NotificationPersistence
from app.service.opportunity_service import OpportunityService

logger = logging.getLogger("divine_vision.opportunity_api")

router = APIRouter(prefix="/opportunities", tags=["Opportunities"], dependencies=[Depends(enforce_rate_limit)])


def get_opportunity_service(db: DatabaseDep, settings: SettingsDep) -> OpportunityService:
    notification_service = NotificationService(NotificationPersistence(db))
    return OpportunityService(db, OpportunityPersistence(db), notification_service, settings)


OpportunityServiceDep = Annotated[OpportunityService, Depends(get_opportunity_service)]


@router.get("", response_model=PaginatedResponse[OpportunityResponse])
async def list_my_opportunities(
    current_employee: CurrentEmployeeDep,
    opportunity_service: OpportunityServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[OpportunityResponse]:
    try:
        from app.core.responses import PaginationMeta

        items, total = await opportunity_service.list_for_employee(current_employee.employee_id, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing opportunities")
        raise


@router.get("/{opportunity_id}", response_model=SuccessResponse[OpportunityResponse])
async def get_opportunity(
    opportunity_id: UUID,
    current_employee: CurrentEmployeeDep,
    opportunity_service: OpportunityServiceDep,
) -> SuccessResponse[OpportunityResponse]:
    try:
        result = await opportunity_service.get_opportunity(current_employee.employee_id, str(opportunity_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching opportunity")
        raise


@router.get("/{opportunity_id}/claims", response_model=SuccessResponse[list[OpportunityClaimResponse]])
async def list_opportunity_claims(
    opportunity_id: UUID,
    current_employee: CurrentEmployeeDep,
    opportunity_service: OpportunityServiceDep,
) -> SuccessResponse[list[OpportunityClaimResponse]]:
    try:
        result = await opportunity_service.list_claims(current_employee.employee_id, str(opportunity_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing opportunity claims")
        raise


@router.post("/{opportunity_id}/status", response_model=SuccessResponse[OpportunityResponse])
async def update_opportunity_status(
    opportunity_id: UUID,
    payload: UpdateOpportunityStatusRequest,
    current_employee: CurrentEmployeeDep,
    opportunity_service: OpportunityServiceDep,
) -> SuccessResponse[OpportunityResponse]:
    try:
        result = await opportunity_service.update_status(
            current_employee.employee_id, str(opportunity_id), payload.status
        )
        return SuccessResponse(data=result, message="Opportunity status updated")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error updating opportunity status")
        raise


@router.post("/{opportunity_id}/resolve", response_model=SuccessResponse[OpportunityResponse])
async def resolve_opportunity_conflict(
    opportunity_id: UUID,
    payload: ResolveConflictRequest,
    _back_office: BackOfficeAuthDep,
    opportunity_service: OpportunityServiceDep,
) -> SuccessResponse[OpportunityResponse]:
    try:
        result = await opportunity_service.resolve_conflict(
            opportunity_id=str(opportunity_id),
            resolved_source_owner_type=payload.resolved_source_owner_type,
            resolved_source_owner_employee_id=payload.resolved_source_owner_employee_id,
            resolved_source_owner_channel_partner_id=payload.resolved_source_owner_channel_partner_id,
            reason=payload.reason,
            resolved_by=payload.resolved_by,
        )
        return SuccessResponse(data=result, message="Opportunity conflict resolved")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error resolving opportunity conflict")
        raise
