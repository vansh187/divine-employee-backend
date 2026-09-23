"""Deal API — /api/v1/deals/* (§20.6 — Opportunity -> Deal, hard Deal Lock)."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentEmployeeDep, DatabaseDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, PaginationMeta, SuccessResponse
from app.persistence.deal_persistence import DealPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.persistence.property_persistence import PropertyPersistence
from app.schemas.deal_schema import CreateDealRequest, DealResponse
from app.service.deal_service import DealService

logger = logging.getLogger("divine_vision.deal_api")

router = APIRouter(prefix="/deals", tags=["Deals"], dependencies=[Depends(enforce_rate_limit)])


def get_deal_service(db: DatabaseDep) -> DealService:
    return DealService(db, DealPersistence(db), OpportunityPersistence(db), PropertyPersistence(db))


DealServiceDep = Annotated[DealService, Depends(get_deal_service)]


@router.post("", response_model=SuccessResponse[DealResponse])
async def create_deal(
    payload: CreateDealRequest,
    current_employee: CurrentEmployeeDep,
    deal_service: DealServiceDep,
) -> SuccessResponse[DealResponse]:
    try:
        result = await deal_service.create_deal(current_employee.employee_id, payload.opportunity_id)
        return SuccessResponse(data=result, message="Deal created and property locked")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error creating deal")
        raise


@router.get("", response_model=PaginatedResponse[DealResponse])
async def list_my_deals(
    current_employee: CurrentEmployeeDep,
    deal_service: DealServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[DealResponse]:
    try:
        items, total = await deal_service.list_for_employee(current_employee.employee_id, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing deals")
        raise


@router.get("/{deal_id}", response_model=SuccessResponse[DealResponse])
async def get_deal(
    deal_id: UUID,
    current_employee: CurrentEmployeeDep,
    deal_service: DealServiceDep,
) -> SuccessResponse[DealResponse]:
    try:
        result = await deal_service.get_deal(current_employee.employee_id, str(deal_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching deal")
        raise


@router.post("/{deal_id}/complete", response_model=SuccessResponse[DealResponse])
async def complete_deal(
    deal_id: UUID,
    current_employee: CurrentEmployeeDep,
    deal_service: DealServiceDep,
) -> SuccessResponse[DealResponse]:
    try:
        result = await deal_service.complete_deal(current_employee.employee_id, str(deal_id))
        return SuccessResponse(data=result, message="Deal completed; property marked SOLD")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error completing deal")
        raise


@router.post("/{deal_id}/cancel", response_model=SuccessResponse[DealResponse])
async def cancel_deal(
    deal_id: UUID,
    current_employee: CurrentEmployeeDep,
    deal_service: DealServiceDep,
) -> SuccessResponse[DealResponse]:
    try:
        result = await deal_service.cancel_deal(current_employee.employee_id, str(deal_id))
        return SuccessResponse(data=result, message="Deal cancelled; deal lock released")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error cancelling deal")
        raise
