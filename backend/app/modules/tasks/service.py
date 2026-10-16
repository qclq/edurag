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
TASK_TYPE_LABELS = {
    "document_ingest": "文档入库",
    "document_reprocess": "文档重新处理",
    "knowledge_base_rebuild": "知识库索引重建",
    "user_import": "用户批量导入",
}


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


def administrator_failure_payload(task: ProcessingTask, *, knowledge_base_name: str) -> dict[str, Any]:
    """Return a non-technical explanation suitable for operational administrators."""
    raw = f"{task.error_code or ''} {task.error_message or ''}".lower()
    type_label = TASK_TYPE_LABELS.get(task.type, "后台处理")
    if any(marker in raw for marker in ("foreign key", "23503", "still referenced", "外键")):
        message = "系统发现相关内容仍被现有问答引用，为避免影响已产生的结果，本次更新已安全停止。"
        action = "请稍后重试；如果持续出现，可先保留旧版本并联系技术支持。"
    elif any(marker in raw for marker in ("embedding", "milvus", "vector", "向量")):
        message = "知识索引服务暂时未能完成处理，原始文档仍然保留。"
        action = "请检查服务状态后重试该任务，或联系技术支持。"
    elif any(marker in raw for marker in ("timeout", "timed out", "超时", "connection")):
        message = "处理过程中连接超时，系统没有完成本次操作。"
        action = "建议稍后重试；多次失败时请联系技术支持。"
    elif any(marker in raw for marker in ("pdf", "docx", "parse", "解析")):
        message = "系统未能读取或解析该文档，可能是文件损坏或格式不受支持。"
        action = "请确认文件可以正常打开，并重新上传常用格式的文件。"
    else:
        message = "系统未能完成本次后台处理，相关原始数据没有被删除。"
        action = "请尝试重新执行；如果仍然失败，请将参考编号提供给技术支持。"
    return {
        "id": str(task.id),
        "referenceNo": task.task_no,
        "type": task.type,
        "typeLabel": type_label,
        "knowledgeBaseId": str(task.knowledge_base_id) if task.knowledge_base_id else None,
        "knowledgeBaseName": knowledge_base_name,
        "status": task.status,
        "title": f"{type_label}未完成",
        "message": message,
        "suggestedAction": action,
        "createdAt": task.created_at,
        "completedAt": task.completed_at,
    }


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
