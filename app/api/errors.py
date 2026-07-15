import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.exceptions import (
    ApprovalRequestNotFoundError,
    InvalidTransitionError,
    NotAuthorizedForDecisionError,
)
from app.observability.redaction import looks_sensitive

logger = logging.getLogger(__name__)

_PROBLEM_MEDIA_TYPE = "application/problem+json"

_TITLES = {
    status.HTTP_400_BAD_REQUEST: "Bad Request",
    status.HTTP_401_UNAUTHORIZED: "Unauthorized",
    status.HTTP_403_FORBIDDEN: "Forbidden",
    status.HTTP_404_NOT_FOUND: "Not Found",
    status.HTTP_409_CONFLICT: "Conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "Validation Error",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "Internal Server Error",
}


def _problem(status_code: int, detail: str, **extra: object) -> JSONResponse:
    """RFC 7807 (application/problem+json) response body."""
    body = {
        "type": "about:blank",
        "title": _TITLES.get(status_code, "Error"),
        "status": status_code,
        "detail": detail,
        **extra,
    }
    return JSONResponse(status_code=status_code, content=body, media_type=_PROBLEM_MEDIA_TYPE)


def _clean_validation_errors(errors: list[dict]) -> list[dict]:
    """`ctx` (Pydantic's internal message-templating context) sometimes carries the
    raw exception object for custom `field_validator`s, which isn't JSON-serializable
    and isn't meant for API consumers anyway — `msg` already has the resolved text.
    Also redacts `input` when the field name looks sensitive (belt-and-suspenders:
    none of our fields are secret-shaped today, but better safe if one ever is)."""
    cleaned = []
    for error in errors:
        error = {k: v for k, v in error.items() if k != "ctx"}
        loc = error.get("loc", ())
        field_name = str(loc[-1]) if loc else ""
        if looks_sensitive(field_name) and "input" in error:
            error["input"] = "[REDACTED]"
        cleaned.append(error)
    return cleaned


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _problem(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The request body or parameters failed validation.",
            errors=_clean_validation_errors(exc.errors()),
        )

    @app.exception_handler(ApprovalRequestNotFoundError)
    async def _not_found_handler(
        request: Request, exc: ApprovalRequestNotFoundError
    ) -> JSONResponse:
        return _problem(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(NotAuthorizedForDecisionError)
    async def _forbidden_handler(
        request: Request, exc: NotAuthorizedForDecisionError
    ) -> JSONResponse:
        return _problem(status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(InvalidTransitionError)
    async def _conflict_handler(request: Request, exc: InvalidTransitionError) -> JSONResponse:
        return _problem(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Full details go to the server-side log only; the client gets nothing that
        # could leak internals (stack traces, file paths, query text, ...).
        logger.exception(
            "Unhandled exception while processing %s %s", request.method, request.url.path
        )
        return _problem(status.HTTP_500_INTERNAL_SERVER_ERROR, "An unexpected error occurred.")
