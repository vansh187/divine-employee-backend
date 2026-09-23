"""Dashboard API — /api/v1/dashboard (requirement §9)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import BusinessClockDep, CurrentEmployeeDep, DatabaseDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import SuccessResponse
from app.persistence.attendance_persistence import AttendancePersistence
from app.persistence.dashboard_persistence import DashboardPersistence
from app.persistence.day_off_persistence import DayOffPersistence
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.lock_persistence import LockPersistence
from app.persistence.site_visit_persistence import SiteVisitPersistence
from app.schemas.dashboard_schema import DashboardResponse
from app.service.dashboard_service import DashboardService

logger = logging.getLogger("divine_vision.dashboard_api")

router = APIRouter(prefix="/dashboard", tags=["Dashboard"], dependencies=[Depends(enforce_rate_limit)])


def get_dashboard_service(db: DatabaseDep, business_clock: BusinessClockDep) -> DashboardService:
    return DashboardService(
        EmployeePersistence(db),
        SiteVisitPersistence(db),
        LockPersistence(db),
        DayOffPersistence(db),
        AttendancePersistence(db),
        DashboardPersistence(db),
        business_clock,
    )


DashboardServiceDep = Annotated[DashboardService, Depends(get_dashboard_service)]


@router.get("", response_model=SuccessResponse[DashboardResponse])
async def get_dashboard(
    current_employee: CurrentEmployeeDep,
    dashboard_service: DashboardServiceDep,
) -> SuccessResponse[DashboardResponse]:
    try:
        result = await dashboard_service.get_dashboard(current_employee.employee_id)
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error building dashboard")
        raise
