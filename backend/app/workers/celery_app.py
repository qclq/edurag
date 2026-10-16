from celery import Celery

from app.core.config import get_settings


settings = get_settings()
celery_app = Celery("edurag", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    imports=("app.workers.document_pipeline", "app.workers.answer_generation", "app.workers.maintenance"),
    beat_schedule={
        "admin-record-retention-hourly": {
            "task": "edurag.retain_admin_records",
            "schedule": 3600.0,
        },
    },
)
