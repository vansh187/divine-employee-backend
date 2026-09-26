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


# Statuses in which an opportunity is still live and can be acted on.
OPEN_STATUSES: tuple[str, ...] = ("NEW", "INTERESTED", "DEAL_IN_PROGRESS", "ACTIVE")

# Forward-only pipeline: NEW -> INTERESTED -> DEAL_IN_PROGRESS -> CONVERTED / DEAL_REJECTED.
# LOST and RELEASED are independent exits available from any open status.
STAGE_RANK: dict[str, int] = {"NEW": 0, "ACTIVE": 0, "INTERESTED": 1, "DEAL_IN_PROGRESS": 2}

_STATUS_ALIASES: dict[str, str] = {
    "CONVERTED": "CONVERTED",
    "DEAL_COMPLETE": "CONVERTED",
    "DEAL_CLOSED": "CONVERTED",
    "DEAL_COMPLETED": "CONVERTED",
    "COMPLETE": "CONVERTED",
    "COMPLETED": "CONVERTED",
    "WON": "CONVERTED",
    "LOST": "LOST",
    "DEAL_REJECTED": "DEAL_REJECTED",
    "REJECTED": "DEAL_REJECTED",
    "DEAL_LOST": "LOST",
    "RELEASED": "RELEASED",
    "RELEASE": "RELEASED",
    "RELEASE_LOCK": "RELEASED",
    "INTERESTED": "INTERESTED",
    "DEAL_IN_PROGRESS": "DEAL_IN_PROGRESS",
    "IN_PROGRESS": "DEAL_IN_PROGRESS",
}


class UpdateOpportunityStatusRequest(BaseModel):
    """Accepts the API values (CONVERTED/LOST/RELEASED) and the UI dropdown labels, case-insensitively.

    ``status`` is normalised to INTERESTED, DEAL_IN_PROGRESS, CONVERTED (deal complete), DEAL_REJECTED, LOST or RELEASED.
    """

    status: str

    @field_validator("status", mode="before")
    @classmethod
    def normalise_status(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("status must be a string")
        key = "_".join(value.replace("-", " ").upper().split())
        normalised = _STATUS_ALIASES.get(key)
        if normalised is None:
            raise ValueError(f"Unsupported status '{value}'")
        return normalised


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
