"""Request/response contracts for the Deal API (§20.6)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class CreateDealRequest(BaseModel):
    opportunity_id: str

    @field_validator("opportunity_id")
    @classmethod
    def validate_opportunity_id(cls, value: str) -> str:
        UUID(value)
        return value


class DealResponse(BaseModel):
    id: str
    opportunity_id: str
    lead_id: str
    project_id: str
    property_id: str
    source_owner_type: str
    source_owner_employee_id: str | None
    source_owner_channel_partner_id: str | None
    handling_employee_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime
