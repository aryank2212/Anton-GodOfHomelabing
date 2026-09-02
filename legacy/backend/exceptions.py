from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import http_exception_handler
from .logging_config import get_logger

logger = get_logger("legacy.exceptions")


class AppException(Exception):
    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: dict | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class NotFoundException(AppException):
    def __init__(self, resource: str = "Resource", resource_id: int | str | None = None):
        msg = f"{resource} not found"
        if resource_id:
            msg += f": {resource_id}"
        super().__init__(msg, code="NOT_FOUND", status_code=404)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="UNAUTHORIZED", status_code=401)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message, code="FORBIDDEN", status_code=403)


class ValidationException(AppException):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="VALIDATION_ERROR", status_code=400, details=details)


class ConflictException(AppException):
    def __init__(self, message: str):
        super().__init__(message, code="CONFLICT", status_code=409)


class RateLimitException(AppException):
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, code="RATE_LIMITED", status_code=429)


ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
}


async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, AppException):
        logger.warning(
            f"AppException: {exc.code} - {exc.message}",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    if isinstance(exc, HTTPException):
        code = ERROR_CODES.get(exc.status_code, "UNKNOWN")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": exc.detail,
                    "details": {},
                }
            },
        )

    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
            }
        },
    )


def register_exception_handlers(app):
    app.add_exception_handler(Exception, global_exception_handler)
