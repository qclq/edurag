import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    error_payload,
    http_error_handler,
    validation_error_handler,
)
from fastapi.exceptions import RequestValidationError


logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version="0.1.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    request.state.request_id = incoming[:128] if incoming else f"req_{uuid.uuid4().hex}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error", exc_info=exc)
    error = AppError(500, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试")
    return JSONResponse(status_code=500, content=error_payload(request, error))


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root():
    return {"name": settings.app_name, "docs": "/docs"}
