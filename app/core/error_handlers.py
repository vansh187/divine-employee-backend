"""Global exception handlers — the final safety net behind each endpoint's own try/except.

Every response body here is a fixed, sanitized string. Raw exception text,
stack traces, SQL fragments and driver error messages are NEVER placed in the
HTTP response — they go to the server log only. This guarantees that a bug or
an unexpected runtime exception anywhere in the stack (persistence, service,
API, or a library like asyncpg/jose/passlib) degrades to a generic 500 instead
of crashing the process or leaking internal details to the caller.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError

logger = logging.getLogger("divine_vision")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        error: dict[str, object] = {"code": exc.code, "message": exc.message}
        if exc.fields:
            error["fields"] = exc.fields
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": error})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field-level validation errors are safe/expected to surface (they only
        # describe the caller's own request shape), unlike internal exceptions.
        safe_errors = [
            {"field": ".".join(str(part) for part in err.get("loc", [])), "message": err.get("msg", "Invalid value")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {"code": "VALIDATION_FAILED", "message": "Request validation failed", "fields": safe_errors},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Catches every runtime exception that slips past service/API try-excepts
        # (DB connectivity drops, driver errors, programming errors, etc.) so the
        # process never crashes a request and no internal detail ever reaches the client.
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
        )
