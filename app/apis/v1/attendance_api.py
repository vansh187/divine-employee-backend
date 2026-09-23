"""Attendance API — /api/v1/attendance/*."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import BusinessClockDep, CurrentEmployeeDep, DatabaseDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, PaginationMeta, SuccessResponse
from app.persistence.attendance_persistence import AttendancePersistence
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.attendance_schema import AttendanceRecordResponse, CheckInRequest
from app.service.attendance_service import AttendanceService

logger = logging.getLogger("divine_vision.attendance_api")

router = APIRouter(prefix="/attendance", tags=["Attendance"], dependencies=[Depends(enforce_rate_limit)])


def get_attendance_service(db: DatabaseDep, business_clock: BusinessClockDep) -> AttendanceService:
    return AttendanceService(
        AttendancePersistence(db), DayOffPersistence(db), SiteVisitPersistence(db), business_clock
    )


AttendanceServiceDep = Annotated[AttendanceService, Depends(get_attendance_service)]


@router.post("/check-in", response_model=SuccessResponse[AttendanceRecordResponse])
async def check_in(
    payload: CheckInRequest,
    current_employee: CurrentEmployeeDep,
    attendance_service: AttendanceServiceDep,
) -> SuccessResponse[AttendanceRecordResponse]:
    try:
        result = await attendance_service.check_in(current_employee.employee_id, payload.latitude, payload.longitude)
        return SuccessResponse(data=result, message="Checked in")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error during check-in")
        raise


@router.post("/check-out", response_model=SuccessResponse[AttendanceRecordResponse])
async def check_out(
    current_employee: CurrentEmployeeDep,
    attendance_service: AttendanceServiceDep,
) -> SuccessResponse[AttendanceRecordResponse]:
    try:
        result = await attendance_service.check_out(current_employee.employee_id)
        return SuccessResponse(data=result, message="Checked out")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error during check-out")
        raise


@router.get("/today", response_model=SuccessResponse[AttendanceRecordResponse | None])
async def get_today_attendance(
    current_employee: CurrentEmployeeDep,
    attendance_service: AttendanceServiceDep,
) -> SuccessResponse[AttendanceRecordResponse | None]:
    try:
        result = await attendance_service.get_today(current_employee.employee_id)
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching today's attendance")
        raise


@router.get("/history", response_model=PaginatedResponse[AttendanceRecordResponse])
async def get_history(
    current_employee: CurrentEmployeeDep,
    attendance_service: AttendanceServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> PaginatedResponse[AttendanceRecordResponse]:
    try:
        items, total = await attendance_service.list_history(current_employee.employee_id, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching attendance history")
        raise


@router.get("/calendar", response_model=SuccessResponse[list[AttendanceRecordResponse]])
async def get_monthly_calendar(
    current_employee: CurrentEmployeeDep,
    attendance_service: AttendanceServiceDep,
    year: int = Query(...),
    month: int = Query(...),
) -> SuccessResponse[list[AttendanceRecordResponse]]:
    try:
        result = await attendance_service.get_monthly_calendar(current_employee.employee_id, year, month)
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching monthly calendar")
        raise
