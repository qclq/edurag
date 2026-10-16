from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_current_user, get_page_params
from app.core.config import Settings, get_settings
from app.core.errors import AppError, paginated, success
from app.core.security import utcnow
from app.db.session import get_db
from app.modules.conversations.models import Conversation, Message, MessageEvent
from app.modules.conversations.schemas import (
    ConversationCreate,
    ConversationUpdate,
    MessageCreate,
    RatingUpdate,
    RegenerationCreate,
)
from app.modules.conversations.service import (
    TERMINAL_MESSAGE_STATUSES,
    append_message_event,
    citation_payload,
    conversation_payload,
    create_conversation,
    generate_message,
    get_conversation_or_404,
    get_message_or_404,
    message_payload,
    submit_message,
    update_rating,
)
from app.modules.operations.service import record_audit
from app.modules.users.models import User
from app.workers.answer_generation import dispatch_answer_generation


router = APIRouter()


def _ensure_personal_history_allowed(user: User) -> None:
    if user.user_type == "external":
        raise AppError(403, "EXTERNAL_HISTORY_FORBIDDEN", "外部人员账号不提供个人历史会话功能")


@router.post("/conversations", status_code=201, summary="创建会话")
def post_conversation(
    payload: ConversationCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = create_conversation(db, user, payload.knowledge_base_ids, payload.title)
    db.commit()
    db.refresh(conversation)
    return success(request, conversation_payload(get_conversation_or_404(db, conversation.id, user)), status_code=201)


@router.get("/conversations", summary="本人会话分页")
def list_conversations(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    kb_id: uuid.UUID | None = Query(default=None, alias="kbId"),
    time_range: str | None = Query(default=None, alias="timeRange"),
    status: str | None = None,
    favorite: bool | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_personal_history_allowed(user)
    statement = (
        select(Conversation)
        .options(selectinload(Conversation.knowledge_bases))
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
    )
    if q:
        statement = statement.where(Conversation.title.ilike(f"%{q}%"))
    if kb_id:
        statement = statement.where(Conversation.knowledge_bases.any(id=kb_id))
    if status:
        statement = statement.where(Conversation.status == status)
    if favorite is not None:
        statement = statement.where(Conversation.is_favorite == favorite)
    days = {"today": 1, "7d": 7, "30d": 30}.get(time_range or "")
    if days:
        statement = statement.where(Conversation.created_at >= utcnow() - timedelta(days=days))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.offset(page.offset).limit(page.page_size)).unique().all()
    return paginated(request, [conversation_payload(item) for item in items], page=page.page, page_size=page.page_size, total=total)


@router.get("/conversations/{conversation_id}", summary="会话详情")
def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return success(request, conversation_payload(get_conversation_or_404(db, conversation_id, user), include_messages=True))


@router.patch("/conversations/{conversation_id}", summary="更新会话")
def patch_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_personal_history_allowed(user)
    conversation = get_conversation_or_404(db, conversation_id, user)
    if payload.title is not None:
        conversation.title = payload.title
    if payload.favorite is not None:
        conversation.is_favorite = payload.favorite
    db.commit()
    return success(request, conversation_payload(conversation))


@router.delete("/conversations/{conversation_id}", status_code=204, summary="删除会话")
def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_personal_history_allowed(user)
    conversation = get_conversation_or_404(db, conversation_id, user)
    conversation.deleted_at = utcnow()
    conversation.status = "deleted"
    db.commit()
    return Response(status_code=204)


@router.delete("/conversations/{conversation_id}/messages", summary="清空会话消息")
def clear_messages(
    conversation_id: uuid.UUID,
    request: Request,
    confirm: bool = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_personal_history_allowed(user)
    conversation = get_conversation_or_404(db, conversation_id, user)
    if not confirm:
        raise AppError(400, "CONFIRMATION_REQUIRED", "请确认清空记录")
    result = db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    conversation.message_count = 0
    conversation.last_message_at = None
    db.commit()
    return success(request, {"deletedCount": result.rowcount or 0})


@router.get("/conversations/{conversation_id}/messages", summary="游标读取消息")
def list_messages(
    conversation_id: uuid.UUID,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = get_conversation_or_404(db, conversation_id, user)
    statement = (
        select(Message)
        .options(selectinload(Message.citations))
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
        .offset(cursor)
        .limit(limit + 1)
    )
    items = db.scalars(statement).unique().all()
    has_more = len(items) > limit
    items = items[:limit]
    return success(request, {"items": [message_payload(item) for item in items], "nextCursor": cursor + limit if has_more else None})


@router.post("/conversations/{conversation_id}/messages", status_code=202, summary="发送消息")
def post_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    conversation = get_conversation_or_404(db, conversation_id, user)
    user_message, assistant, created = submit_message(db, conversation, payload)
    db.commit()
    if created:
        if settings.task_dispatch_mode == "inline":
            generate_message(db, assistant, user, settings)
        elif settings.environment != "test":
            try:
                dispatch_answer_generation(str(assistant.id), settings)
            except RuntimeError:
                pass
    if assistant is None:
        raise AppError(409, "MESSAGE_SUBMISSION_INCOMPLETE", "消息已提交，但回答记录尚未创建，请稍后刷新")
    db.refresh(assistant)
    return success(
        request,
        {
            "userMessage": message_payload(user_message),
            "assistantMessage": message_payload(assistant),
            "streamUrl": f"/api/v1/messages/{assistant.id}/events",
            "idempotentReplay": not created,
        },
        status_code=202,
    )


@router.get("/messages/{message_id}/events", summary="回答 SSE 事件")
def message_events(
    message_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    get_message_or_404(db, message_id, user)
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
                select(MessageEvent).where(MessageEvent.message_id == message_id, MessageEvent.sequence > after).order_by(MessageEvent.sequence)
            ).all()
            if events:
                idle = 0
                for event in events:
                    after = event.sequence
                    yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(jsonable_encoder(event.payload), ensure_ascii=False)}\n\n"
            else:
                idle += 1
                yield f"event: heartbeat\ndata: {json.dumps({'at': utcnow().isoformat()})}\n\n"
            current = db.get(Message, message_id)
            if current and current.status in TERMINAL_MESSAGE_STATUSES and not events:
                break
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/messages/{message_id}/stop", summary="停止回答生成")
def stop_message(
    message_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = get_message_or_404(db, message_id, user)
    if message.role != "assistant":
        raise AppError(409, "MESSAGE_NOT_GENERATABLE", "只能停止助手回答")
    if message.status in TERMINAL_MESSAGE_STATUSES:
        return success(request, message_payload(message))
    message.status = "stopped"
    message.completed_at = utcnow()
    append_message_event(db, message, "stopped", {"messageId": str(message.id), "content": message.content, "status": "stopped"})
    db.commit()
    return success(request, message_payload(message))


@router.post("/messages/{message_id}/regenerations", status_code=202, summary="重新生成回答")
def regenerate_message(
    message_id: uuid.UUID,
    payload: RegenerationCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    previous = get_message_or_404(db, message_id, user)
    if previous.role != "assistant" or not previous.parent_message_id:
        raise AppError(409, "MESSAGE_NOT_REGENERATABLE", "该消息不能重新生成")
    latest_version = db.scalar(
        select(func.max(Message.generation_version)).where(
            Message.conversation_id == previous.conversation_id,
            Message.parent_message_id == previous.parent_message_id,
        )
    ) or 1
    regenerated = Message(
        conversation_id=previous.conversation_id,
        parent_message_id=previous.parent_message_id,
        role="assistant",
        status="submitted",
        generation_version=latest_version + 1,
    )
    db.add(regenerated)
    db.flush()
    append_message_event(db, regenerated, "stage", {"messageId": str(regenerated.id), "status": "submitted", "stage": "understanding", "label": "正在重新生成"})
    if payload.replace_current:
        previous.status = "stopped"
    db.commit()
    if settings.task_dispatch_mode == "inline":
        generate_message(db, regenerated, user, settings)
    elif settings.environment != "test":
        try:
            dispatch_answer_generation(str(regenerated.id), settings)
        except RuntimeError:
            pass
    return success(request, {"assistantMessage": message_payload(regenerated), "streamUrl": f"/api/v1/messages/{regenerated.id}/events"}, status_code=202)


@router.put("/messages/{message_id}/rating", summary="评价回答")
def rate_message(
    message_id: uuid.UUID,
    payload: RatingUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = get_message_or_404(db, message_id, user)
    if message.role != "assistant":
        raise AppError(409, "MESSAGE_NOT_RATEABLE", "只能评价助手回答")
    rating, feedback = update_rating(db, message, user, payload)
    db.commit()
    return success(request, {"value": rating.value if rating else "none", "reason": rating.reason if rating else None, "comment": rating.comment if rating else None, "feedbackId": str(feedback.id) if feedback else None})


@router.get("/messages/{message_id}/citations", summary="回答引用列表")
def message_citations(
    message_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = get_message_or_404(db, message_id, user)
    return success(request, [citation_payload(item) for item in message.citations])
