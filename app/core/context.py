"""Authenticated-caller context, derived only from a verified JWT."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentEmployee:
    """Identity of the calling employee. Never accept employee_id from the request body."""

    employee_id: str
    employee_code: str
    email: str
