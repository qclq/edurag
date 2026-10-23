import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError, success
from app.core.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from app.db.session import get_db


router = APIRouter()


def _office_converter_available(binary: str) -> bool:
    return bool(shutil.which(binary) or Path(binary).is_file())


def _pipeline_runtime_status(settings: Settings) -> dict[str, object]:
    if settings.task_dispatch_mode == "inline":
        return {
            "status": "ready",
            "dispatchMode": "inline",
            "broker": "not_required",
            "worker": "inline",
            "workerCount": 1,
        }
    try:
        from app.workers.celery_app import celery_app

        with celery_app.connection_for_read() as connection:
            connection.ensure_connection(max_retries=0)
    except Exception:
        return {
            "status": "degraded",
            "dispatchMode": "celery",
            "broker": "unavailable",
            "worker": "unknown",
            "workerCount": 0,
        }
    try:
        replies = celery_app.control.inspect(
            timeout=settings.task_queue_health_timeout_seconds
        ).ping()
    except Exception:
        replies = None
    worker_count = len(replies or {})
    return {
        "status": "ready" if worker_count else "degraded",
        "dispatchMode": "celery",
        "broker": "available",
        "worker": "available" if worker_count else "unavailable",
        "workerCount": worker_count,
    }


@router.get("/bootstrap", summary="读取公开系统启动配置")
def bootstrap(request: Request, settings: Settings = Depends(get_settings)):
    sso_enabled = settings.sso_provider != "disabled"
    return success(
        request,
        {
            "systemName": settings.app_name,
            "description": settings.app_description,
            "loginMethods": {
                "local": {"enabled": True},
                "sso": {
                    "enabled": sso_enabled,
                    "provider": settings.sso_provider if sso_enabled else None,
                    "channels": ["student", "staff"] if sso_enabled else [],
                },
            },
            "captchaPolicy": {
                "enabled": settings.captcha_enabled,
                "length": settings.captcha_length,
                "expiresIn": settings.captcha_ttl_seconds,
            },
            "sessionPolicy": {"rememberDays": settings.remember_session_days},
            "passwordPolicy": {
                "minLength": PASSWORD_MIN_LENGTH,
                "maxLength": PASSWORD_MAX_LENGTH,
            },
            "questionLimits": {"maxLength": settings.question_max_length},
            "uploadLimits": {
                "maxFileSizeBytes": settings.upload_max_file_mb * 1024 * 1024,
                "maxFiles": settings.upload_max_files,
                "allowedExtensions": settings.upload_extensions,
            },
            "featureFlags": {
                "localLogin": True,
                "ssoLogin": sso_enabled,
                "profileEdit": True,
                "localPasswordChange": True,
                "knowledgeBases": True,
                "documentIngestion": True,
                "streamingQa": True,
                "notifications": True,
            },
            "serviceStatus": {
                "status": "operational",
                "embedding": {
                    "provider": settings.embedding_provider,
                    "model": settings.embedding_model,
                    "configured": settings.embedding_provider == "local" or bool(settings.qwen_api_key),
                },
                "ocr": {
                    "provider": "qwen",
                    "model": settings.qwen_ocr_model,
                    "configured": bool(settings.qwen_api_key),
                },
                "officeConverter": {
                    "provider": "libreoffice",
                    "binary": settings.libreoffice_binary,
                    "configured": _office_converter_available(settings.libreoffice_binary),
                },
                "answering": {
                    "provider": settings.llm_provider,
                    "model": settings.deepseek_model if settings.llm_provider == "deepseek" else "extractive",
                    "configured": settings.llm_provider == "extractive" or bool(settings.deepseek_api_key),
                },
            },
        },
    )


@router.get("/health/live", include_in_schema=False)
def live(request: Request):
    return success(request, {"status": "ok"})


@router.get("/health/ready", include_in_schema=False)
def ready(request: Request, db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise AppError(503, "SYSTEM_NOT_READY", "数据库连接不可用") from exc
    return success(request, {"status": "ready", "database": "available"})


@router.get("/health/pipeline", include_in_schema=False)
def pipeline_ready(request: Request, settings: Settings = Depends(get_settings)):
    status = _pipeline_runtime_status(settings)
    if status["status"] != "ready":
        raise AppError(
            503,
            "PIPELINE_NOT_READY",
            "文档处理后台服务不可用，请检查 Redis 和 Celery worker",
            details=status,
        )
    return success(request, status)
