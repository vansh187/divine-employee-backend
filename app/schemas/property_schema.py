"""Request/response contracts for the Projects/Properties API."""

from datetime import datetime
from decimal import Decimal


from pydantic import BaseModel


class ProjectResponse(BaseModel):
    id: str
    name: str
    location: str | None
    phase: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class PropertyResponse(BaseModel):
    id: str
    project_id: str
    plot_no: str
    unit_type: str | None
    area_sqft: Decimal | None
    status: str
    created_at: datetime
    updated_at: datetime


class InventoryItemResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    project_location: str | None
    plot_no: str
    unit_type: str | None
    area_sqft: Decimal | None
    status: str
    updated_at: datetime
