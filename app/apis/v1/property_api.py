"""Projects/Properties API — /api/v1/properties/*."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentEmployeeDep, DatabaseDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import PaginatedResponse, PaginationMeta, SuccessResponse
from app.persistence.property_persistence import PropertyPersistence
from app.schemas.property_schema import InventoryItemResponse, ProjectResponse, PropertyResponse
from app.service.property_service import PropertyService

logger = logging.getLogger("divine_vision.property_api")

router = APIRouter(prefix="/properties", tags=["Properties"], dependencies=[Depends(enforce_rate_limit)])


def get_property_service(db: DatabaseDep) -> PropertyService:
    return PropertyService(PropertyPersistence(db))


PropertyServiceDep = Annotated[PropertyService, Depends(get_property_service)]


@router.get("/projects", response_model=SuccessResponse[list[ProjectResponse]])
async def list_projects(
    current_employee: CurrentEmployeeDep,
    property_service: PropertyServiceDep,
) -> SuccessResponse[list[ProjectResponse]]:
    try:
        result = await property_service.list_projects()
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing projects")
        raise


@router.get("/inventory", response_model=PaginatedResponse[InventoryItemResponse])
async def get_inventory(
    current_employee: CurrentEmployeeDep,
    property_service: PropertyServiceDep,
    project_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, description="AVAILABLE | LOCKED | DEAL_LOCKED | SOLD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PaginatedResponse[InventoryItemResponse]:
    try:
        items, total = await property_service.get_inventory(
            str(project_id) if project_id else None, status, page, page_size
        )
        total_pages = max(1, (total + page_size - 1) // page_size)
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return PaginatedResponse(data=items, pagination=meta)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching inventory")
        raise


@router.get("/projects/{project_id}/plots", response_model=SuccessResponse[list[PropertyResponse]])
async def list_plots(
    project_id: UUID,
    current_employee: CurrentEmployeeDep,
    property_service: PropertyServiceDep,
) -> SuccessResponse[list[PropertyResponse]]:
    try:
        result = await property_service.list_properties(str(project_id))
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error listing plots")
        raise
