"""Shared FastAPI dependency providers.

APIs depend on these functions (not on concrete classes) so wiring stays in
one place: settings -> security helpers -> db -> current-employee identity.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.context import CurrentEmployee
from app.core.datetime_utils import BusinessClock
from app.core.email_sender import EmailSender, build_email_sender
from app.core.exceptions import ForbiddenError
from app.core.security import TOKEN_TYPE_ACCESS, PasswordHasher, TokenService
from app.persistence.db_persistence import Database

_bearer_scheme = HTTPBearer(auto_error=True)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database(request: Request) -> Database:
    return request.app.state.db


DatabaseDep = Annotated[Database, Depends(get_database)]


def get_token_service(settings: SettingsDep) -> TokenService:
    return TokenService(settings)


TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


def get_password_hasher() -> PasswordHasher:
    return PasswordHasher()


PasswordHasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]


def get_email_sender(settings: SettingsDep) -> EmailSender:
    return build_email_sender(settings)


EmailSenderDep = Annotated[EmailSender, Depends(get_email_sender)]


def get_business_clock(settings: SettingsDep) -> BusinessClock:
    return BusinessClock(settings)


BusinessClockDep = Annotated[BusinessClock, Depends(get_business_clock)]


def get_current_employee(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
    token_service: TokenServiceDep,
) -> CurrentEmployee:
    payload = token_service.decode_token(credentials.credentials, expected_type=TOKEN_TYPE_ACCESS)
    return CurrentEmployee(
        employee_id=payload["sub"],
        employee_code=payload.get("employee_code", ""),
        email=payload.get("email", ""),
    )


CurrentEmployeeDep = Annotated[CurrentEmployee, Depends(get_current_employee)]


def require_back_office_key(
    settings: SettingsDep,
    x_back_office_key: Annotated[str | None, Header()] = None,
) -> None:
    """OP-D04 assumed default: conflict resolution is gated by a shared back-office
    key rather than employee JWT, since the Employee Portal has no Manager/Admin role."""
    if not x_back_office_key or not hmac.compare_digest(
        x_back_office_key.encode(), settings.back_office_api_key.encode()
    ):
        raise ForbiddenError("Back-office authorization required")


BackOfficeAuthDep = Annotated[None, Depends(require_back_office_key)]


def require_cron_secret(
    settings: SettingsDep,
    x_cron_secret: Annotated[str | None, Header()] = None,
    key: Annotated[str | None, Query()] = None,
) -> None:
    """Gates the scheduler-only sweep endpoint; disabled while CRON_SECRET is unset.

    Accepts the secret as the `X-Cron-Secret` header or, for simple schedulers
    that can only hit a plain URL, a `?key=` query parameter. A URL secret can
    end up in request logs; that's acceptable here because the sweep only
    expires rows that are already past their expiry, so a leaked key can't
    change any outcome — rotate CRON_SECRET if it leaks anyway.
    """
    provided = x_cron_secret or key
    if not settings.cron_secret or not provided or not hmac.compare_digest(
        provided.encode(), settings.cron_secret.encode()
    ):
        raise ForbiddenError("Scheduler authorization required")


CronAuthDep = Annotated[None, Depends(require_cron_secret)]
