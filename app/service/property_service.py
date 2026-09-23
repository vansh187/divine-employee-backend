"""Business logic for browsing Projects/Properties/Plots (requirement §1, §9 — read-only inventory)."""

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.persistence.property_persistence import PropertyPersistence
from app.schemas.property_schema import InventoryItemResponse, ProjectResponse, PropertyResponse

_VALID_STATUSES = {"AVAILABLE", "LOCKED", "DEAL_LOCKED", "SOLD"}


class PropertyService:
    def __init__(self, property_persistence: PropertyPersistence) -> None:
        self._property_persistence = property_persistence

    async def list_projects(self) -> list[ProjectResponse]:
        rows = await self._property_persistence.list_projects()
        return [ProjectResponse(**row) for row in rows]

    async def list_properties(self, project_id: str) -> list[PropertyResponse]:
        project = await self._property_persistence.get_project_by_id(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        rows = await self._property_persistence.list_properties_by_project(project_id)
        return [PropertyResponse(**row) for row in rows]

    async def get_inventory(
        self, project_id: str | None, status: str | None, page: int, page_size: int
    ) -> tuple[list[InventoryItemResponse], int]:
        if status is not None and status not in _VALID_STATUSES:
            raise ValidationFailedError(f"status must be one of {sorted(_VALID_STATUSES)}")

        if project_id is not None:
            project = await self._property_persistence.get_project_by_id(project_id)
            if project is None:
                raise NotFoundError("Project not found")

        rows, total = await self._property_persistence.list_inventory(project_id, status, page, page_size)
        items = [
            InventoryItemResponse(
                id=str(row["id"]),
                project_id=str(row["project_id"]),
                project_name=row["project_name"],
                project_location=row["project_location"],
                plot_no=row["plot_no"],
                unit_type=row["unit_type"],
                area_sqft=row["area_sqft"],
                width_m=row["width_m"],
                length_m=row["length_m"],
                area_sqm=row["area_sqm"],
                area_sqyd=row["area_sqyd"],
                status=row["status"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        return items, total
