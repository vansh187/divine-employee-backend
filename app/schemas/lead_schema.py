"""Request/response contracts for the Leads API."""

from datetime import datetime

from pydantic import BaseModel, Field


class LeadResponse(BaseModel):
    id: str
    name: str
    normalized_phone: str
    email: str | None
    source: str
    lifecycle_status: str
    first_visit_at: datetime | None
    latest_visit_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LogFollowUpRequest(BaseModel):
    action_type: str = Field(pattern="^(CALL_LOGGED|NEXT_VISIT_SCHEDULED|PROPOSAL_SENT|NOTE)$")
    notes: str | None = None
    reference: str | None = None


class FollowUpActionResponse(BaseModel):
    id: str
    lead_id: str
    employee_id: str
    action_type: str
    notes: str | None
    reference: str | None
    logged_at: datetime


class ActiveLockResponse(BaseModel):
    lead_lock_id: str
    lead_id: str
    lead_name: str
    expires_at: datetime
    property_id: str | None
    plot_no: str | None
    project_name: str | None
