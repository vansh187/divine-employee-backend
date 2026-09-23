"""Request/response contracts for the Site Visit API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Mirrors the `site_visit_outcome` Postgres enum — anything else is a 422, not a DB error.
SiteVisitOutcome = Literal["INTERESTED", "NOT_INTERESTED", "FOLLOW_UP_REQUIRED", "PROPOSAL_REQUESTED", "NO_SHOW", "OTHER"]


class CreateSiteVisitRequest(BaseModel):
    visitor_name: str = Field(min_length=1)
    phone: str = Field(min_length=8)
    email: str | None = None
    project_id: str
    property_id: str | None = None
    visit_at: datetime
    notes: str | None = None
    attachments: list[str] = Field(default_factory=list)
    outcome: SiteVisitOutcome | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)

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
    project_id: str
    property_id: str | None
    visit_at: datetime
    notes: str | None
    attachments: list[str]
    outcome: str | None
    lead_name: str | None = None
    project_name: str | None = None
    plot_no: str | None = None
    created_at: datetime
    updated_at: datetime
