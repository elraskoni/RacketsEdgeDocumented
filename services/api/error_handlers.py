"""
Standardised error response handlers for all RacketEdge FastAPI apps.

All error responses share a single JSON shape:
    {"error": "<code>", "detail": "<message>"}

Usage:
    from .error_handlers import register_error_handlers
    register_error_handlers(app)
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("racket-edge")


def _error_body(error: str, detail: Any = None) -> dict:
    body: dict = {"error": error}
    if detail is not None:
        body["detail"] = detail
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """
        Wraps FastAPI/Starlette HTTPExceptions into the standard shape.
        The `detail` field is used as the error code when it's a plain string
        (e.g. "player_not_found"), otherwise a generic code from the status is used.
        """
        if isinstance(exc.detail, str):
            error_code = exc.detail
            detail = exc.detail
        else:
            # dict/list detail (e.g. from custom raises) — preserve as detail
            error_code = _status_to_code(exc.status_code)
            detail = exc.detail

        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(error_code, detail if detail != error_code else None),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """
        Pydantic / query-param validation failures → 422 with structured errors.
        """
        errors = []
        for e in exc.errors():
            errors.append({
                "field": ".".join(str(p) for p in e.get("loc", [])),
                "message": e.get("msg", ""),
                "type": e.get("type", ""),
            })
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", errors),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """
        Catch-all for unhandled exceptions. Logs the full traceback server-side.
        In dev mode (APP_ENV=dev) the exception type and message are included in
        the response so they appear in the browser/frontend error display.
        """
        import os
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        body = _error_body("internal_server_error")
        if os.getenv("APP_ENV", "dev") == "dev":
            body["debug_detail"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(status_code=500, content=body)


def _status_to_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_error",
        429: "rate_limit_exceeded",
        500: "internal_server_error",
        503: "service_unavailable",
    }.get(status_code, f"http_{status_code}")
