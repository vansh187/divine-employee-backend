"""Request/response contracts for the Site Visit API — one field per input on the
employee portal's "Log a Site Visit" form."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator
from app.schemas.opportunity_schema import OpportunityResponse

# Mirrors the `site_visit_outcome` Postgres enum — anything else is a 422, not a DB error.
SiteVisitOutcome = Literal["INTERESTED", "NOT_INTERESTED", "FOLLOW_UP_REQUIRED", "PROPOSAL_REQUESTED", "NO_SHOW", "OTHER"]


class CreateSiteVisitRequest(BaseModel):
    visitor_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=8, max_length=30)
    email: EmailStr | None = None
    project_id: str
    property_id: str | None = None  # "No specific plot" -> null
    # Visit date + time from the form, ideally with an offset
    # (e.g. 2026-09-23T23:41:00+05:30). A value without an offset is taken as
    # business-local time (Asia/Kolkata), never UTC.
    visit_at: datetime
    notes: str | None = Field(default=None, max_length=5000)
    attachments: list[str] = Field(default_factory=list)
    outcome: SiteVisitOutcome | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("email", "property_id", "notes", "outcome", "idempotency_key", mode="before")
    @classmethod
    def blank_to_none(cls, value: Any) -> Any:
        # HTML forms submit untouched optional inputs/selects as "" — treat that as "not provided".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("visitor_name", "phone", mode="before")
    @classmethod
    def strip_whitespace(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        # Raises pydantic ValidationError (-> 422) on malformed input, never a raw 500.
        # Normalized to canonical lowercase-hyphenated form so string comparisons
        # against DB-decoded ids are reliable.
        return str(UUID(value))

    @field_validator("property_id")
    @classmethod
    def validate_property_id(cls, value: str | None) -> str | None:
        if value is not None:
            return str(UUID(value))
        return value


class SiteVisitResponse(BaseModel):
    id: str
    employee_id: str
    lead_id: str
    # Exactly as entered on this visit's form.
    visitor_name: str | None = None
    visitor_phone: str | None = None
    visitor_email: str | None = None
    project_id: str
    property_id: str | None
    visit_at: datetime
    notes: str | None
    attachments: list[str]
    outcome: str | None
    # The Lead's master name (may differ from visitor_name on repeat visits).
    lead_name: str | None = None
    project_name: str | None = None
    plot_no: str | None = None
    created_at: datetime
    updated_at: datetime
    # Historical visits keep the opportunity linked by their claim evidence.
    opportunity: OpportunityResponse | None = None
    can_update_opportunity: bool = False
    # Set on the create response when the lead/plot is held by another employee: the visit is
    # saved, but takes no lock and creates no opportunity.
    lead_held: bool = False
    waitlisted: bool = False
    held_until: datetime | None = None
