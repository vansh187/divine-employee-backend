"""Auth API — /api/v1/auth/*.

Only request/response wiring lives here: dependency resolution, calling the
service, translating results/errors into the HTTP response. No business logic,
no SQL.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentEmployeeDep, DatabaseDep, PasswordHasherDep, TokenServiceDep
from app.core.exceptions import AppError
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import SuccessResponse
from app.core.security import PasswordHasher, TokenService
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.db_persistence import Database
from app.schemas.auth_schema import (
    EmployeeProfileResponse,
    LoginRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.service.auth_service import AuthService

logger = logging.getLogger("divine_vision.auth_api")

router = APIRouter(prefix="/auth", tags=["Auth"], dependencies=[Depends(enforce_rate_limit)])


def get_auth_service(
    db: DatabaseDep,
    token_service: TokenServiceDep,
    password_hasher: PasswordHasherDep,
) -> AuthService:
    employee_persistence = EmployeePersistence(db)
    return AuthService(employee_persistence, token_service, password_hasher)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/login", response_model=SuccessResponse[TokenPairResponse])
async def login(payload: LoginRequest, auth_service: AuthServiceDep) -> SuccessResponse[TokenPairResponse]:
    try:
        result = await auth_service.login(payload.email, payload.password)
        return SuccessResponse(data=result, message="Login successful")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error during login")
        raise


@router.post("/refresh", response_model=SuccessResponse[TokenPairResponse])
async def refresh_token(payload: RefreshRequest, auth_service: AuthServiceDep) -> SuccessResponse[TokenPairResponse]:
    try:
        result = await auth_service.refresh(payload.refresh_token)
        return SuccessResponse(data=result, message="Token refreshed")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error during token refresh")
        raise


@router.post("/logout", response_model=SuccessResponse[None])
async def logout(payload: RefreshRequest, auth_service: AuthServiceDep) -> SuccessResponse[None]:
    try:
        await auth_service.logout(payload.refresh_token)
        return SuccessResponse(data=None, message="Logged out")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error during logout")
        raise


@router.get("/me", response_model=SuccessResponse[EmployeeProfileResponse])
async def get_my_profile(
    current_employee: CurrentEmployeeDep,
    auth_service: AuthServiceDep,
) -> SuccessResponse[EmployeeProfileResponse]:
    try:
        result = await auth_service.get_profile(current_employee.employee_id)
        return SuccessResponse(data=result)
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error fetching profile")
        raise
