"""Error responses carry a machine-readable ``code`` that the UI translates.

The backend never returns user-facing text: the UI looks up ``errors.<code>`` in its locale
files and may substitute ``params`` into the message.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorResponse(BaseModel):
    code: str
    params: dict[str, Any] = Field(default_factory=dict)


class ApiError(Exception):
    """Raise from an endpoint to return ``{"code": ..., "params": ...}`` with a status."""

    def __init__(self, code: str, status_code: int = 400, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.params = params


HTTP_STATUS_CODES: dict[int, str] = {
    404: "not_found",
    405: "method_not_allowed",
}


def _response(status_code: int, code: str, params: dict[str, Any] | None = None) -> JSONResponse:
    body = ErrorResponse(code=code, params=params or {})
    return JSONResponse(status_code=status_code, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _response(exc.status_code, exc.code, exc.params)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _response(exc.status_code, HTTP_STATUS_CODES.get(exc.status_code, "http_error"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(p) for p in err["loc"]) for err in exc.errors()]
        return _response(422, "validation_error", {"fields": fields})

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, __: Exception) -> JSONResponse:
        return _response(500, "internal_error")
