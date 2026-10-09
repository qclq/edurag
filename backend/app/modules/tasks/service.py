from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.core.security import utcnow
from app.modules.tasks.models import ProcessingTask, TaskEvent


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "partial_success"}


def task_payload(task: ProcessingTask, *, include_events: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(task.id),
        "taskNo": task.task_no,
        "type": task.type,
        "knowledgeBaseId": str(task.knowledge_base_id) if task.knowledge_base_id else None,
        "documentId": str(task.document_id) if task.document_id else None,
        "documentVersionId": str(task.document_version_id) if task.document_version_id else None,
        "status": task.status,
        "stage": task.stage,
        "progressPercent": task.progress_percent,
        "progressText": task.progress_text,
        "error": (
            {"code": task.error_code, "message": task.error_message} if task.error_code else None
        ),
        "createdBy": str(task.created_by),
        "createdAt": task.created_at,
        "startedAt": task.started_at,
        "completedAt": task.completed_at,
        "cancelRequestedAt": task.cancel_requested_at,
    }
    if include_events:
        data["events"] = [event_payload(event) for event in task.events]
    return data


def event_payload(event: TaskEvent) -> dict[str, Any]:
    return {
        "id": str(event.sequence),
        "sequence": event.sequence,
        "event": event.event_type,
        "stage": event.stage,
        "data": event.payload or {},
        "occurredAt": event.occurred_at,
    }


def create_task(
    db: Session,
    *,
    type: str,
    created_by,
    knowledge_base_id=None,
    document_id=None,
    document_version_id=None,
    idempotency_key: str | None = None,
    progress_text: str = "等待处理",
) -> tuple[ProcessingTask, bool]:
    if idempotency_key:
        existing = db.scalar(
            select(ProcessingTask).where(
                ProcessingTask.created_by == created_by,
                ProcessingTask.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing, False
    now = utcnow()
    task = ProcessingTask(
        task_no=f"TASK-{now:%Y%m%d}-{uuid.uuid4().hex[:10].upper()}",
        type=type,
        created_by=created_by,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        document_version_id=document_version_id,
        idempotency_key=idempotency_key,
        progress_text=progress_text,
    )
    db.add(task)
    db.flush()
    append_event(db, task, "queued", payload={"status": "queued", "text": progress_text})
    return task, True


def append_event(
    db: Session,
    task: ProcessingTask,
    event_type: str,
    *,
    stage: str | None = None,
    payload: dict[str, Any] | None = None,
) -> TaskEvent:
    highest = db.scalar(select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task.id)) or 0
    event = TaskEvent(
        task_id=task.id,
        sequence=highest + 1,
        event_type=event_type,
        stage=stage,
        payload=jsonable_encoder(payload or {}),
    )
    db.add(event)
    db.flush()
    return event


def update_task(
    db: Session,
    task: ProcessingTask,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress_percent: int | None = None,
    progress_text: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    if status:
        task.status = status
    if stage is not None:
        task.stage = stage
    task.progress_percent = progress_percent
    if progress_text is not None:
        task.progress_text = progress_text
    task.error_code = error_code
    task.error_message = error_message
    if task.status == "running" and task.started_at is None:
        task.started_at = utcnow()
    if task.status in TERMINAL_STATUSES:
        task.completed_at = utcnow()
    append_event(
        db,
        task,
        "error" if status == "failed" else ("completed" if status in TERMINAL_STATUSES else "progress"),
        stage=stage,
        payload={
            "status": task.status,
            "progressPercent": task.progress_percent,
            "text": task.progress_text,
            "errorCode": error_code,
            "errorMessage": error_message,
        },
    )


def get_task_or_404(db: Session, task_id) -> ProcessingTask:
    task = db.scalar(
        select(ProcessingTask)
        .options(selectinload(ProcessingTask.events), selectinload(ProcessingTask.items))
        .where(ProcessingTask.id == task_id)
    )
    if not task:
        raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
    return task
