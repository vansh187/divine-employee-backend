"""Domain exceptions carrying deterministic error codes.

Every business-rule failure raises one of these instead of a bare HTTPException,
so the API layer can translate them into a consistent JSON error envelope with
a stable `code` the frontend can branch on (see requirement §11: "Site Visit
creation must return deterministic conflicts such as DAY_OFF_CONFLICT,
LEAD_LOCKED and PROPERTY_LOCKED").
"""


class AppError(Exception):
    """Base class for all business/domain errors.

    `fields` (optional) uses the same `[{"field": ..., "message": ...}]` shape as
    request-validation errors, so the frontend can show a message on one input.
    """

    def __init__(
        self, code: str, message: str, status_code: int = 400, fields: list[dict[str, str]] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.fields = fields
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(code="NOT_FOUND", message=message, status_code=404)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(code="UNAUTHORIZED", message=message, status_code=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Access denied") -> None:
        super().__init__(code="FORBIDDEN", message=message, status_code=403)


class ValidationFailedError(AppError):
    def __init__(self, message: str = "Validation failed", fields: list[dict[str, str]] | None = None) -> None:
        super().__init__(code="VALIDATION_FAILED", message=message, status_code=422, fields=fields)


class ConflictError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=409)


class DayOffConflictError(ConflictError):
    def __init__(self, message: str = "This date is a frozen Day Off; site visits are blocked") -> None:
        super().__init__(code="DAY_OFF_CONFLICT", message=message)


class LeadLockedError(ConflictError):
    def __init__(self, message: str = "This lead is currently locked to another employee") -> None:
        super().__init__(code="LEAD_LOCKED", message=message)


class PropertyLockedError(ConflictError):
    def __init__(self, message: str = "This property is currently locked to another employee") -> None:
        super().__init__(code="PROPERTY_LOCKED", message=message)


class DayOffAllowanceExceededError(ConflictError):
    def __init__(self, message: str = "Weekly Day-Off allowance already used") -> None:
        super().__init__(code="DAY_OFF_ALLOWANCE_EXCEEDED", message=message)


class DayOffVisitExistsError(ConflictError):
    def __init__(self, message: str = "A site visit already exists on this date") -> None:
        super().__init__(code="DAY_OFF_VISIT_EXISTS", message=message)


class OpportunityConflictError(ConflictError):
    def __init__(self, message: str = "An active opportunity already exists for another source owner") -> None:
        super().__init__(code="OPPORTUNITY_CONFLICT", message=message)


class DealLockedError(ConflictError):
    def __init__(self, message: str = "This property is under a confirmed deal lock") -> None:
        super().__init__(code="DEAL_LOCKED", message=message)


class EmailAlreadyRegisteredError(ConflictError):
    def __init__(self, message: str = "An employee account already exists with this email") -> None:
        super().__init__(code="EMAIL_ALREADY_REGISTERED", message=message)


class EmployeeIdAlreadyRegisteredError(ConflictError):
    def __init__(self, message: str = "This employee ID is already registered") -> None:
        super().__init__(code="EMPLOYEE_ID_ALREADY_REGISTERED", message=message)


class InvalidOtpError(AppError):
    def __init__(self, message: str = "The code is incorrect") -> None:
        super().__init__(code="INVALID_OTP", message=message, status_code=422)


class OtpExpiredError(AppError):
    def __init__(self, message: str = "The code has expired. Request a new one.") -> None:
        super().__init__(code="OTP_EXPIRED", message=message, status_code=410)


class EmailDeliveryError(AppError):
    def __init__(self, message: str = "We couldn't send the verification email. Please try again shortly.") -> None:
        super().__init__(code="EMAIL_DELIVERY_FAILED", message=message, status_code=503)


class ServiceUnavailableError(AppError):
    def __init__(self, message: str = "The service is temporarily unavailable. Please try again shortly.") -> None:
        super().__init__(code="SERVICE_UNAVAILABLE", message=message, status_code=503)


class RateLimitExceededError(AppError):
    def __init__(self, message: str = "Too many requests, please try again later") -> None:
        super().__init__(code="RATE_LIMIT_EXCEEDED", message=message, status_code=429)


class DuplicateRequestError(AppError):
    def __init__(self, message: str = "Duplicate request detected") -> None:
        super().__init__(code="DUPLICATE_REQUEST", message=message, status_code=409)
