from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors
        self.details = details
        self.headers = headers


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def success(request: Request, data: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({"data": data, "requestId": request_id(request)}),
    )


def paginated(
    request: Request,
    data: Any,
    *,
    page: int,
    page_size: int,
    total: int,
    status_code: int = 200,
    meta: dict[str, Any] | None = None,
) -> JSONResponse:
    """Return the common list envelope used by every collection endpoint."""
    total_pages = (total + page_size - 1) // page_size if total else 0
    payload = {
        "data": data,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": total_pages,
        },
        "requestId": request_id(request),
    }
    if meta is not None:
        payload["meta"] = meta
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
    )


def error_payload(request: Request, error: AppError) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "fieldErrors": error.field_errors,
            "details": error.details,
        },
        "requestId": request_id(request),
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_payload(request, exc)),
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields: dict[str, str] = {}
    for issue in exc.errors():
        location = [str(part) for part in issue["loc"] if part not in {"body", "query", "path"}]
        fields[".".join(location) or "request"] = issue["msg"]
    error = AppError(422, "VALIDATION_ERROR", "请求参数校验失败", field_errors=fields)
    return await app_error_handler(request, error)


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    error = AppError(exc.status_code, "HTTP_ERROR", str(exc.detail))
    return await app_error_handler(request, error)
