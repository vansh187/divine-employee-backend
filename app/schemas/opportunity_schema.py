"""Request/response contracts for the Opportunity Engine API (REQ-25)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class OpportunityResponse(BaseModel):
    id: str
    lead_id: str
    project_id: str
    property_id: str | None
    source_owner_type: str
    source_owner_employee_id: str | None
    source_owner_channel_partner_id: str | None
    handling_employee_id: str | None
    source: str
    status: str
    attribution_status: str
    locked_at: datetime
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class OpportunityClaimResponse(BaseModel):
    id: str
    claimant_type: str
    claimant_employee_id: str | None
    claimant_channel_partner_id: str | None
    claim_type: str
    evidence_site_visit_id: str | None
    qualifying_at: datetime
    submitted_at: datetime


class UpdateOpportunityStatusRequest(BaseModel):
    status: str = Field(pattern="^(CONVERTED|LOST|RELEASED)$")


class ResolveConflictRequest(BaseModel):
    resolved_source_owner_type: str = Field(pattern="^(EMPLOYEE|CHANNEL_PARTNER)$")
    resolved_source_owner_employee_id: str | None = None
    resolved_source_owner_channel_partner_id: str | None = None
    reason: str = Field(min_length=1)
    resolved_by: str = Field(min_length=1)

    @field_validator("resolved_source_owner_employee_id", "resolved_source_owner_channel_partner_id")
    @classmethod
    def validate_uuid(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None

    @model_validator(mode="after")
    def validate_owner_reference(self) -> "ResolveConflictRequest":
        # Mirrors chk_source_owner_reference so a mismatch is a 422, not a DB error.
        is_employee = self.resolved_source_owner_type == "EMPLOYEE"
        has_employee = self.resolved_source_owner_employee_id is not None
        has_partner = self.resolved_source_owner_channel_partner_id is not None
        if is_employee and (not has_employee or has_partner):
            raise ValueError("EMPLOYEE owner requires resolved_source_owner_employee_id only")
        if not is_employee and (not has_partner or has_employee):
            raise ValueError("CHANNEL_PARTNER owner requires resolved_source_owner_channel_partner_id only")
        return self
