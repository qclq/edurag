from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_page_params, require_permissions
from app.core.errors import AppError, paginated, success
from app.core.config import Settings, get_settings
from app.core.security import utcnow
from app.db.session import get_db
from app.modules.knowledge_bases.service import can_access, get_kb_or_404
from app.modules.operations.service import record_audit
from app.modules.tasks.models import ProcessingTask, TaskEvent
from app.modules.tasks.schemas import RetryTask
from app.modules.tasks.service import TERMINAL_STATUSES, create_task, event_payload, get_task_or_404, task_payload, update_task
from app.modules.users.models import User
from app.workers.document_pipeline import dispatch_document_pipeline
from app.workers.maintenance import dispatch_knowledge_base_rebuild


router = APIRouter()


def _ensure_task_scope(db: Session, task: ProcessingTask, user: User) -> None:
    if task.knowledge_base_id:
        kb = get_kb_or_404(db, task.knowledge_base_id)
        if not can_access(kb, user, "manage"):
            raise AppError(404, "TASK_NOT_FOUND", "任务不存在")


@router.get("/tasks", summary="处理任务分页")
def list_tasks(
    request: Request,
    task_no: str | None = Query(default=None, alias="taskNo"),
    type: str | None = None,
    kb_id: uuid.UUID | None = Query(default=None, alias="kbId"),
    status: str | None = None,
    creator_id: uuid.UUID | None = Query(default=None, alias="creatorId"),
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("task.read")),
    db: Session = Depends(get_db),
):
    statement = select(ProcessingTask).options(selectinload(ProcessingTask.events)).order_by(ProcessingTask.created_at.desc())
    if task_no:
        statement = statement.where(ProcessingTask.task_no.ilike(f"%{task_no}%"))
    if type:
        statement = statement.where(ProcessingTask.type == type)
    if kb_id:
        statement = statement.where(ProcessingTask.knowledge_base_id == kb_id)
    if status:
        statement = statement.where(ProcessingTask.status == status)
    if creator_id:
        statement = statement.where(ProcessingTask.created_by == creator_id)
    visible = []
    for task in db.scalars(statement).unique().all():
        try:
            _ensure_task_scope(db, task, user)
            visible.append(task)
        except AppError:
            continue
    items = visible[page.offset : page.offset + page.page_size]
    return paginated(request, [task_payload(item, include_events=False) for item in items], page=page.page, page_size=page.page_size, total=len(visible))


@router.get("/tasks/{task_id}", summary="任务详情")
def get_task(
    task_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("task.read")),
    db: Session = Depends(get_db),
):
    task = get_task_or_404(db, task_id)
    _ensure_task_scope(db, task, user)
    return success(request, task_payload(task))


@router.get("/tasks/{task_id}/events", summary="任务事件流")
def task_events(
    task_id: uuid.UUID,
    user: User = Depends(require_permissions("task.read")),
    db: Session = Depends(get_db),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    task = get_task_or_404(db, task_id)
    _ensure_task_scope(db, task, user)
    try:
        after = int(last_event_id or 0)
    except ValueError as exc:
        raise AppError(400, "SSE_EVENT_ID_INVALID", "Last-Event-ID 格式不正确") from exc

    def stream():
        nonlocal after
        idle = 0
        while idle < 900:
            db.rollback()
            events = db.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.sequence > after).order_by(TaskEvent.sequence)
            ).all()
            if events:
                idle = 0
                for event in events:
                    after = event.sequence
                    body = json.dumps(jsonable_encoder(event_payload(event)["data"]), ensure_ascii=False)
                    yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {body}\n\n"
            else:
                idle += 1
                yield f"event: heartbeat\ndata: {json.dumps({'at': utcnow().isoformat()})}\n\n"
            current = db.get(ProcessingTask, task_id)
            if current and current.status in TERMINAL_STATUSES and not events:
                break
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/tasks/{task_id}/retry", status_code=202, summary="重试失败任务")
def retry_task(
    task_id: uuid.UUID,
    payload: RetryTask,
    request: Request,
    user: User = Depends(require_permissions("task.retry")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    original = get_task_or_404(db, task_id)
    _ensure_task_scope(db, original, user)
    if original.status not in {"failed", "cancelled"}:
        raise AppError(409, "TASK_NOT_RETRYABLE", "只有失败或已取消的任务可以重试")
    retried, _ = create_task(
        db,
        type=original.type,
        created_by=user.id,
        knowledge_base_id=original.knowledge_base_id,
        document_id=original.document_id,
        document_version_id=original.document_version_id,
        progress_text=f"从 {payload.from_stage or original.stage or '安全阶段'} 重试",
    )
    record_audit(db, actor=user, module="task", action="retry", target_type="task", target_id=original.id, request=request, after={"newTaskId": str(retried.id), "fromStage": payload.from_stage})
    db.commit()
    if settings.environment != "test":
        try:
            if retried.document_version_id:
                dispatch_document_pipeline(str(retried.id), settings)
            elif retried.type == "knowledge_base_rebuild":
                dispatch_knowledge_base_rebuild(str(retried.id), settings)
        except RuntimeError:
            pass
    return success(request, task_payload(retried, include_events=False), status_code=202)


@router.post("/tasks/{task_id}/cancel", summary="取消任务")
def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("task.cancel")),
    db: Session = Depends(get_db),
):
    task = get_task_or_404(db, task_id)
    _ensure_task_scope(db, task, user)
    if task.status in TERMINAL_STATUSES:
        raise AppError(409, "TASK_ALREADY_FINISHED", "任务已结束，不能取消")
    task.cancel_requested_at = utcnow()
    if task.status == "queued":
        update_task(db, task, status="cancelled", progress_text="任务已取消")
    record_audit(db, actor=user, module="task", action="cancel", target_type="task", target_id=task.id, request=request)
    db.commit()
    return success(request, task_payload(task, include_events=False))
