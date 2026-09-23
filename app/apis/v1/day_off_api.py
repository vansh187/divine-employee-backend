"""Weekly Day-Off API — /api/v1/day-off/* (requirement §7-8)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import BusinessClockDep, CurrentEmployeeDep, DatabaseDep, SettingsDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, PaginationMeta, SuccessResponse
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.day_off_schema import DayOffResponse, SelectDayOffRequest
from app.service.day_off_service import DayOffService

logger = logging.getLogger("divine_vision.day_off_api")

router = APIRouter(prefix="/day-off", tags=["Day Off"], dependencies=[Depends(enforce_rate_limit)])


def get_day_off_service(db: DatabaseDep, business_clock: BusinessClockDep, settings: SettingsDep) -> DayOffService:
    return DayOffService(DayOffPersistence(db), SiteVisitPersistence(db), business_clock, settings)


DayOffServiceDep = Annotated[DayOffService, Depends(get_day_off_service)]


@router.get("/current-week", response_model=SuccessResponse[DayOffResponse | None])
async def get_current_week(
    current_employee: CurrentEmployeeDep,
    day_off_service: DayOffServiceDep,
) -> SuccessResponse[DayOffResponse | None]:
    try:
        result = await day_off_service.get_current_week(current_employee.employee_id)
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching current week day off")
        raise


@router.post("/select", response_model=SuccessResponse[DayOffResponse])
async def select_day_off(
    payload: SelectDayOffRequest,
    current_employee: CurrentEmployeeDep,
    day_off_service: DayOffServiceDep,
) -> SuccessResponse[DayOffResponse]:
    try:
        result = await day_off_service.select_and_freeze(current_employee.employee_id, payload.day_off_date)
        return SuccessResponse(data=result, message="Day Off frozen for this week")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error selecting day off")
        raise


@router.get("/history", response_model=PaginatedResponse[DayOffResponse])
async def get_history(
    current_employee: CurrentEmployeeDep,
    day_off_service: DayOffServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[DayOffResponse]:
    try:
        items, total = await day_off_service.list_history(current_employee.employee_id, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching day off history")
        raise
