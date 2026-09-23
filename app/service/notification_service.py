"""Business logic for notifications.

Other services (site visit, day off, opportunity, ...) call `emit()` to raise
an activity/notification entry as part of their own transactional flow.
"""

from typing import Any

from app.core.exceptions import NotFoundError
from app.core.responses import PaginationMeta
from app.persistence.notification_persistence import NotificationPersistence
from app.schemas.notification_schema import NotificationResponse


class NotificationService:
    def __init__(self, notification_persistence: NotificationPersistence) -> None:
        self._notification_persistence = notification_persistence

    async def emit(
        self,
        employee_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str | None,
        message: str,
        connection: Any = None,
    ) -> None:
        await self._notification_persistence.create(
            employee_id, event_type, entity_type, entity_id, message, connection=connection
        )

    async def list_for_employee(
        self, employee_id: str, page: int, page_size: int
    ) -> tuple[list[NotificationResponse], PaginationMeta]:
        rows, total = await self._notification_persistence.list_for_employee(employee_id, page, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        items = [NotificationResponse(**row) for row in rows]
        meta = PaginationMeta(page=page, page_size=page_size, total_items=total, total_pages=total_pages)
        return items, meta

    async def mark_read(self, notification_id: str, employee_id: str) -> None:
        updated = await self._notification_persistence.mark_read(notification_id, employee_id)
        if not updated:
            raise NotFoundError("Notification not found or already read")
