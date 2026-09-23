"""Signup API — /api/v1/auth/signup/* (public; no bearer token).

Self-serve employee signup with email OTP verification. Only request/response
wiring lives here; rules are in SignupService.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.deps import (
    BusinessClockDep,
    DatabaseDep,
    EmailSenderDep,
    PasswordHasherDep,
    SettingsDep,
    TokenServiceDep,
)
from app.core.exceptions import AppError
from app.core.otp import OtpCodec
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import SuccessResponse
from app.persistence.employee_persistence import EmployeePersistence
from app.persistence.signup_persistence import SignupPersistence
from app.schemas.auth_schema import TokenPairResponse
from app.schemas.signup_schema import ResendOtpRequest, SignupPendingResponse, SignupRequest, VerifyOtpRequest
from app.service.auth_service import AuthService
from app.service.signup_service import SignupService

logger = logging.getLogger("divine_vision.signup_api")

router = APIRouter(prefix="/auth/signup", tags=["Signup"], dependencies=[Depends(enforce_rate_limit)])


def get_signup_service(
    db: DatabaseDep,
    token_service: TokenServiceDep,
    password_hasher: PasswordHasherDep,
    email_sender: EmailSenderDep,
    business_clock: BusinessClockDep,
    settings: SettingsDep,
) -> SignupService:
    employee_persistence = EmployeePersistence(db)
    return SignupService(
        db=db,
        signup_persistence=SignupPersistence(db),
        employee_persistence=employee_persistence,
        auth_service=AuthService(employee_persistence, token_service, password_hasher),
        password_hasher=password_hasher,
        otp_codec=OtpCodec(settings),
        email_sender=email_sender,
        business_clock=business_clock,
        settings=settings,
    )


SignupServiceDep = Annotated[SignupService, Depends(get_signup_service)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SuccessResponse[SignupPendingResponse])
async def start_signup(payload: SignupRequest, signup_service: SignupServiceDep) -> SuccessResponse[SignupPendingResponse]:
    try:
        result = await signup_service.start_signup(payload)
        return SuccessResponse(data=result, message="Verification code sent")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error starting signup")
        raise


@router.post("/resend-otp", response_model=SuccessResponse[SignupPendingResponse])
async def resend_otp(payload: ResendOtpRequest, signup_service: SignupServiceDep) -> SuccessResponse[SignupPendingResponse]:
    try:
        result = await signup_service.resend_code(payload.email)
        return SuccessResponse(data=result, message="Verification code sent")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error resending signup code")
        raise


@router.post("/verify-otp", response_model=SuccessResponse[TokenPairResponse])
async def verify_otp(payload: VerifyOtpRequest, signup_service: SignupServiceDep) -> SuccessResponse[TokenPairResponse]:
    try:
        result = await signup_service.verify(payload.email, payload.otp)
        return SuccessResponse(data=result, message="Account verified")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error verifying signup code")
        raise
