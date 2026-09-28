"""Forgot password API — /api/v1/auth/forgot-password*, /api/v1/auth/reset-password (public; no bearer token).

Only request/response wiring lives here; rules are in PasswordResetService.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.core.deps import DatabaseDep, EmailSenderDep, PasswordHasherDep, SettingsDep
from app.core.exceptions import AppError
from app.core.otp import OtpCodec
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import SuccessResponse
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.password_reset_persistence import PasswordResetPersistence
from app.schemas.password_reset_schema import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    VerifyResetOtpRequest,
    VerifyResetOtpResponse,
)
from app.service.password_reset_service import PasswordResetService

logger = logging.getLogger("divine_vision.password_reset_api")

router = APIRouter(prefix="/auth", tags=["Forgot password"], dependencies=[Depends(enforce_rate_limit)])


def get_password_reset_service(
    db: DatabaseDep,
    password_hasher: PasswordHasherDep,
    email_sender: EmailSenderDep,
    settings: SettingsDep,
) -> PasswordResetService:
    return PasswordResetService(
        db=db,
        password_reset_persistence=PasswordResetPersistence(db),
        employee_persistence=EmployeePersistence(db),
        password_hasher=password_hasher,
        otp_codec=OtpCodec(settings),
        email_sender=email_sender,
        settings=settings,
    )


PasswordResetServiceDep = Annotated[PasswordResetService, Depends(get_password_reset_service)]


@router.post("/forgot-password", response_model=SuccessResponse[ForgotPasswordResponse])
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    service: PasswordResetServiceDep,
) -> SuccessResponse[ForgotPasswordResponse]:
    try:
        request_ip = request.client.host if request.client else None
        # The code email is sent after the response, so the reply looks the
        # same (content and timing) whether or not the email has an account.
        result = await service.request_code(payload.email, request_ip, background_tasks.add_task)
        return SuccessResponse(data=result, message="If this email has an account, a reset code has been sent")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error requesting a password reset code")
        raise


@router.post("/forgot-password/verify-otp", response_model=SuccessResponse[VerifyResetOtpResponse])
async def verify_reset_otp(
    payload: VerifyResetOtpRequest, service: PasswordResetServiceDep
) -> SuccessResponse[VerifyResetOtpResponse]:
    try:
        result = await service.verify_code(payload.email, payload.otp)
        return SuccessResponse(data=result, message="Code verified")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error verifying a password reset code")
        raise


@router.post("/reset-password", response_model=SuccessResponse[ResetPasswordResponse])
async def reset_password(
    payload: ResetPasswordRequest, background_tasks: BackgroundTasks, service: PasswordResetServiceDep
) -> SuccessResponse[ResetPasswordResponse]:
    try:
        result = await service.reset_password(payload.reset_token, payload.new_password, background_tasks.add_task)
        return SuccessResponse(data=result, message="Password updated")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error resetting a password")
        raise
