"""Notifications API — /api/v1/notifications/*."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentEmployeeDep, DatabaseDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, SuccessResponse
from app.persistence.notification_persistence import NotificationPersistence
from app.schemas.notification_schema import NotificationResponse
from app.service.notification_service import NotificationService

logger = logging.getLogger("divine_vision.notification_api")

router = APIRouter(prefix="/notifications", tags=["Notifications"], dependencies=[Depends(enforce_rate_limit)])


def get_notification_service(db: DatabaseDep) -> NotificationService:
    return NotificationService(NotificationPersistence(db))


NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]


@router.get("", response_model=PaginatedResponse[NotificationResponse])
async def list_notifications(
    current_employee: CurrentEmployeeDep,
    notification_service: NotificationServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[NotificationResponse]:
    try:
        items, meta = await notification_service.list_for_employee(current_employee.employee_id, page, page_size)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing notifications")
        raise


@router.post("/{notification_id}/read", response_model=SuccessResponse[None])
async def mark_notification_read(
    notification_id: UUID,
    current_employee: CurrentEmployeeDep,
    notification_service: NotificationServiceDep,
) -> SuccessResponse[None]:
    try:
        await notification_service.mark_read(str(notification_id), current_employee.employee_id)
        return SuccessResponse(data=None, message="Notification marked as read")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error marking notification read")
        raise
