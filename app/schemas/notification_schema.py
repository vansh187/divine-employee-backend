"""Request/response contracts for the Notifications API."""

from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    event_type: str
    entity_type: str
    entity_id: str | None
    message: str
    read_at: datetime | None
    created_at: datetime
