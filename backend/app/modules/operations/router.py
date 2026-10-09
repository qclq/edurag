from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.deps import PageParams, get_current_user, get_page_params, is_super_admin, require_permissions
from app.core.config import Settings, get_settings
from app.core.errors import AppError, paginated, success
from app.core.security import utcnow
from app.db.session import get_db
from app.modules.conversations.models import Conversation, Message, MessageCitation, MessageRating
from app.modules.conversations.service import message_payload
from app.modules.documents.models import Document
from app.modules.feedback.models import Feedback, FeedbackEvent
from app.modules.feedback.schemas import FeedbackResolve
from app.modules.knowledge_bases.service import can_access, get_kb_or_404, list_accessible
from app.modules.operations.models import AuditLog, Notification, SystemSetting
from app.modules.operations.schemas import ReportCreate, SettingsUpdate
from app.modules.operations.service import notify, record_audit
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import create_task, task_payload
from app.modules.users.models import User


admin_router = APIRouter()
notification_router = APIRouter()


def _range_start(value: str):
    return utcnow() - timedelta(days={"today": 1, "7d": 7, "30d": 30}.get(value, 7))


def feedback_payload(item: Feedback, *, detailed: bool = False) -> dict:
    data = {
        "id": str(item.id),
        "messageId": str(item.message_id) if item.message_id else None,
        "citationId": str(item.citation_id) if item.citation_id else None,
        "conversationId": str(item.conversation_id) if item.conversation_id else None,
        "knowledgeBaseId": str(item.knowledge_base_id) if item.knowledge_base_id else None,
        "createdBy": str(item.created_by),
        "type": item.type,
        "comment": item.comment,
        "status": item.status,
        "assigneeId": str(item.assignee_id) if item.assignee_id else None,
        "resolution": item.resolution,
        "resolutionNote": item.resolution_note,
        "notifyUser": item.notify_user,
        "resolvedAt": item.resolved_at,
        "createdAt": item.created_at,
        "updatedAt": item.updated_at,
    }
    if detailed:
        data["events"] = [
            {
                "id": str(event.id),
                "actorId": str(event.actor_id),
                "fromStatus": event.from_status,
                "toStatus": event.to_status,
                "note": event.note,
                "relatedDocumentId": str(event.related_document_id) if event.related_document_id else None,
                "occurredAt": event.occurred_at,
            }
            for event in item.events
        ]
    return data


@admin_router.get("/dashboard", summary="管理概览")
def dashboard(
    request: Request,
    range: str = Query(default="7d", pattern="^(today|7d|30d)$"),
    user: User = Depends(require_permissions("dashboard.read")),
    db: Session = Depends(get_db),
):
    start = _range_start(range)
    super_admin = is_super_admin(user)
    managed_ids = {kb.id for kb in list_accessible(db, user, "manage")}
    conversations = db.scalars(
        select(Conversation)
        .options(selectinload(Conversation.knowledge_bases))
        .where(Conversation.created_at >= start, Conversation.deleted_at.is_(None))
    ).unique().all()
    if not super_admin:
        conversations = [
            item for item in conversations
            if managed_ids.intersection(kb.id for kb in item.knowledge_bases)
        ]
    conversation_ids = {item.id for item in conversations}
    assistant_messages = db.scalars(
        select(Message).where(
            Message.role == "assistant",
            Message.created_at >= start,
            Message.conversation_id.in_(conversation_ids) if conversation_ids else Message.id.is_(None),
        )
    ).all()
    message_ids = {item.id for item in assistant_messages}
    ratings = db.scalars(
        select(MessageRating).where(
            MessageRating.created_at >= start,
            MessageRating.message_id.in_(message_ids) if message_ids else MessageRating.id.is_(None),
        )
    ).all()
    tasks = db.scalars(select(ProcessingTask).where(ProcessingTask.created_at >= start)).all()
    documents = db.scalars(select(Document).where(Document.deleted_at.is_(None))).all()
    if not super_admin:
        tasks = [item for item in tasks if item.knowledge_base_id in managed_ids]
        documents = [item for item in documents if item.knowledge_base_id in managed_ids]
    daily = defaultdict(lambda: {"questions": 0, "answered": 0, "noAnswer": 0})
    for message in assistant_messages:
        key = message.created_at.date().isoformat()
        daily[key]["questions"] += 1
        if message.status == "completed":
            daily[key]["answered"] += 1
        elif message.status == "no_answer":
            daily[key]["noAnswer"] += 1
    helpful = sum(item.value == "helpful" for item in ratings)
    return success(
        request,
        {
            "range": range,
            "metrics": {
                "conversationCount": len(conversations),
                "questionCount": len(assistant_messages),
                "answerRate": round(sum(item.status == "completed" for item in assistant_messages) / max(1, len(assistant_messages)), 4),
                "satisfactionRate": round(helpful / max(1, len(ratings)), 4),
                "documentCount": len(documents),
                "failedTaskCount": sum(item.status == "failed" for item in tasks),
            },
            "trend": [{"date": key, **value} for key, value in sorted(daily.items())],
            "satisfaction": {"helpful": helpful, "unhelpful": sum(item.value == "unhelpful" for item in ratings), "unrated": max(0, len(assistant_messages) - len(ratings))},
            "taskStatus": dict(Counter(item.status for item in tasks)),
            "providerStatus": None,
        },
    )


@admin_router.get("/knowledge-gaps", summary="知识缺口分页")
def knowledge_gaps(
    request: Request,
    range: str = Query(default="7d", pattern="^(today|7d|30d)$"),
    status: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("qa.analytics")),
    db: Session = Depends(get_db),
):
    messages = db.scalars(
        select(Message)
        .options(joinedload(Message.conversation).selectinload(Conversation.knowledge_bases))
        .where(Message.role == "assistant", Message.status == "no_answer", Message.created_at >= _range_start(range))
        .order_by(Message.created_at.desc())
    ).unique().all()
    if not is_super_admin(user):
        messages = [
            item for item in messages
            if any(can_access(kb, user, "manage") for kb in item.conversation.knowledge_bases)
        ]
    groups = {}
    for message in messages:
        parent = db.get(Message, message.parent_message_id)
        question = parent.content.strip() if parent else "未知问题"
        key = question.lower()[:100]
        item = groups.setdefault(key, {"id": key, "question": question, "count": 0, "lastOccurredAt": message.created_at, "status": "open", "suggestedAction": "补充相关文档或调整问题表达"})
        item["count"] += 1
        if message.created_at > item["lastOccurredAt"]:
            item["lastOccurredAt"] = message.created_at
    items = list(groups.values())
    if status:
        items = [item for item in items if item["status"] == status]
    items.sort(key=lambda item: (-item["count"], item["question"]))
    return paginated(request, items[page.offset : page.offset + page.page_size], page=page.page, page_size=page.page_size, total=len(items))


@admin_router.get("/anomalies", summary="异常任务分页")
def anomalies(
    request: Request,
    type: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("task.read")),
    db: Session = Depends(get_db),
):
    statement = select(ProcessingTask).where(ProcessingTask.status == "failed").order_by(ProcessingTask.created_at.desc())
    if type:
        statement = statement.where(ProcessingTask.type == type)
    all_items = db.scalars(statement).all()
    if not is_super_admin(user):
        managed_ids = {kb.id for kb in list_accessible(db, user, "manage")}
        all_items = [item for item in all_items if item.knowledge_base_id in managed_ids]
    total = len(all_items)
    items = all_items[page.offset : page.offset + page.page_size]
    return paginated(request, [task_payload(item, include_events=False) for item in items], page=page.page, page_size=page.page_size, total=total)


@admin_router.post("/reports", status_code=202, summary="创建分析报告导出任务")
def create_report(
    payload: ReportCreate,
    request: Request,
    user: User = Depends(require_permissions("dashboard.read")),
    db: Session = Depends(get_db),
):
    task, _ = create_task(db, type=f"report_{payload.type}", created_by=user.id, progress_text="等待生成报告")
    record_audit(db, actor=user, module="analytics", action="report.export", target_type="task", target_id=task.id, request=request, after=payload.model_dump())
    db.commit()
    return success(request, task_payload(task, include_events=False), status_code=202)


@admin_router.get("/qa-records", summary="管理问答记录")
def qa_records(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("qa.read")),
    db: Session = Depends(get_db),
):
    statement = (
        select(Message)
        .options(
            joinedload(Message.conversation).selectinload(Conversation.knowledge_bases),
            selectinload(Message.citations),
        )
        .where(Message.role == "assistant")
        .order_by(Message.created_at.desc())
    )
    if status:
        statement = statement.where(Message.status == status)
    if q:
        statement = statement.where(Message.content.ilike(f"%{q}%"))
    visible = [
        item
        for item in db.scalars(statement).unique().all()
        if is_super_admin(user)
        or any(can_access(kb, user, "manage") for kb in item.conversation.knowledge_bases)
    ]
    total = len(visible)
    items = visible[page.offset : page.offset + page.page_size]
    data = []
    for item in items:
        parent = db.get(Message, item.parent_message_id)
        data.append({"id": str(item.id), "conversationId": str(item.conversation_id), "userId": str(item.conversation.user_id), "question": parent.content if parent else None, "answerPreview": item.content[:240], "status": item.status, "citationCount": len(item.citations), "modelName": item.model_name, "createdAt": item.created_at, "completedAt": item.completed_at})
    return paginated(request, data, page=page.page, page_size=page.page_size, total=total)


@admin_router.get("/qa-records/{message_id}", summary="完整问答审计详情")
def qa_record_detail(
    message_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("qa.content.read")),
    db: Session = Depends(get_db),
):
    message = db.scalar(
        select(Message)
        .options(
            selectinload(Message.citations),
            selectinload(Message.events),
            joinedload(Message.conversation).selectinload(Conversation.knowledge_bases),
        )
        .where(Message.id == message_id, Message.role == "assistant")
    )
    if not message or (
        not is_super_admin(user)
        and not any(can_access(kb, user, "manage") for kb in message.conversation.knowledge_bases)
    ):
        raise AppError(404, "QA_RECORD_NOT_FOUND", "问答记录不存在")
    parent = db.get(Message, message.parent_message_id)
    record_audit(db, actor=user, module="qa", action="content.read", target_type="message", target_id=message.id, request=request)
    db.commit()
    return success(request, {"question": message_payload(parent) if parent else None, "answer": message_payload(message), "events": [{"sequence": event.sequence, "type": event.event_type, "data": event.payload, "occurredAt": event.occurred_at} for event in message.events]})


@admin_router.get("/feedbacks", summary="反馈分页")
def list_feedbacks(
    request: Request,
    status: str | None = None,
    type: str | None = None,
    kb_id: uuid.UUID | None = Query(default=None, alias="kbId"),
    assignee_id: uuid.UUID | None = Query(default=None, alias="assigneeId"),
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("feedback.read")),
    db: Session = Depends(get_db),
):
    statement = select(Feedback).order_by(Feedback.created_at.desc())
    if status:
        statement = statement.where(Feedback.status == status)
    if type:
        statement = statement.where(Feedback.type == type)
    if kb_id:
        statement = statement.where(Feedback.knowledge_base_id == kb_id)
    if assignee_id:
        statement = statement.where(Feedback.assignee_id == assignee_id)
    visible = [item for item in db.scalars(statement).all() if _feedback_visible(db, item, user)]
    total = len(visible)
    items = visible[page.offset : page.offset + page.page_size]
    return paginated(request, [feedback_payload(item) for item in items], page=page.page, page_size=page.page_size, total=total)


def _feedback_visible(db: Session, item: Feedback, user: User) -> bool:
    if is_super_admin(user):
        return True
    if item.knowledge_base_id:
        try:
            return can_access(get_kb_or_404(db, item.knowledge_base_id), user, "manage")
        except AppError:
            return False
    if item.conversation_id:
        conversation = db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.knowledge_bases))
            .where(Conversation.id == item.conversation_id)
        )
        return bool(conversation and any(can_access(kb, user, "manage") for kb in conversation.knowledge_bases))
    return False


def _get_feedback(db: Session, feedback_id, user: User) -> Feedback:
    item = db.scalar(select(Feedback).options(selectinload(Feedback.events)).where(Feedback.id == feedback_id))
    if not item or not _feedback_visible(db, item, user):
        raise AppError(404, "FEEDBACK_NOT_FOUND", "反馈不存在")
    return item


@admin_router.get("/feedbacks/{feedback_id}", summary="反馈详情")
def get_feedback(
    feedback_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("feedback.read")),
    db: Session = Depends(get_db),
):
    return success(request, feedback_payload(_get_feedback(db, feedback_id, user), detailed=True))


@admin_router.post("/feedbacks/{feedback_id}/claim", summary="领取反馈")
def claim_feedback(
    feedback_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("feedback.manage")),
    db: Session = Depends(get_db),
):
    item = _get_feedback(db, feedback_id, user)
    if item.status == "resolved":
        raise AppError(409, "FEEDBACK_ALREADY_RESOLVED", "反馈已解决")
    if item.assignee_id and item.assignee_id != user.id:
        raise AppError(409, "FEEDBACK_ALREADY_CLAIMED", "反馈已被其他管理员领取")
    previous = item.status
    item.assignee_id = user.id
    item.status = "processing"
    db.add(FeedbackEvent(feedback_id=item.id, actor_id=user.id, from_status=previous, to_status="processing", note="领取处理"))
    record_audit(db, actor=user, module="feedback", action="claim", target_type="feedback", target_id=item.id, request=request)
    db.commit()
    return success(request, feedback_payload(item))


@admin_router.post("/feedbacks/{feedback_id}/resolve", summary="解决反馈")
def resolve_feedback(
    feedback_id: uuid.UUID,
    payload: FeedbackResolve,
    request: Request,
    user: User = Depends(require_permissions("feedback.manage")),
    db: Session = Depends(get_db),
):
    item = _get_feedback(db, feedback_id, user)
    previous = item.status
    item.status = "resolved"
    item.assignee_id = user.id
    item.resolution = payload.resolution
    item.resolution_note = payload.note
    item.notify_user = payload.notify_user
    item.resolved_at = utcnow()
    if payload.related_document_ids:
        existing = set(db.scalars(select(Document.id).where(Document.id.in_(payload.related_document_ids))).all())
        if existing != set(payload.related_document_ids):
            raise AppError(400, "RELATED_DOCUMENT_INVALID", "部分关联文档不存在")
    related = payload.related_document_ids[0] if payload.related_document_ids else None
    db.add(FeedbackEvent(feedback_id=item.id, actor_id=user.id, from_status=previous, to_status="resolved", note=payload.note, related_document_id=related))
    if payload.notify_user:
        notify(db, item.created_by, type="feedback_resolved", title="你的反馈已处理", body=payload.note, target_type="feedback", target_id=item.id)
    record_audit(db, actor=user, module="feedback", action="resolve", target_type="feedback", target_id=item.id, request=request, after=payload.model_dump())
    db.commit()
    return success(request, feedback_payload(item, detailed=True))


@admin_router.get("/settings", summary="系统设置")
def get_settings_api(
    request: Request,
    user: User = Depends(require_permissions("system.read")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    rows = db.scalars(select(SystemSetting).where(SystemSetting.is_secret.is_(False)).order_by(SystemSetting.key)).all()
    version = max((item.row_version for item in rows), default=1)
    return success(request, {"values": {item.key: item.value_json for item in rows}, "version": version, "services": {"qwenEmbedding": {"model": settings.embedding_model, "configured": bool(settings.qwen_api_key)}, "deepSeek": {"model": settings.deepseek_model, "configured": bool(settings.deepseek_api_key)}, "milvus": {"enabled": settings.milvus_enabled, "alias": settings.milvus_collection_alias}, "storage": {"backend": settings.storage_backend}}})


@admin_router.patch("/settings", summary="更新系统设置")
def patch_settings(
    payload: SettingsUpdate,
    request: Request,
    user: User = Depends(require_permissions("system.update")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(SystemSetting).where(SystemSetting.is_secret.is_(False))).all()
    current_version = max((item.row_version for item in rows), default=1)
    if payload.version != current_version:
        raise AppError(409, "VERSION_CONFLICT", "系统设置已被修改，请刷新后重试")
    existing = {item.key: item for item in rows}
    forbidden = {key for key in payload.values if any(word in key.lower() for word in ("secret", "password", "api_key", "apikey", "token"))}
    if forbidden:
        raise AppError(400, "SECRET_SETTING_FORBIDDEN", "密钥不能通过普通设置接口写入", details={"keys": sorted(forbidden)})
    for key, value in payload.values.items():
        row = existing.get(key)
        if row is None:
            row = SystemSetting(key=key, value_json=value, updated_by=user.id, row_version=current_version + 1)
            db.add(row)
        else:
            row.value_json = value
            row.updated_by = user.id
            row.row_version = current_version + 1
    record_audit(db, actor=user, module="system", action="settings.update", target_type="settings", target_id="global", request=request, before={item.key: item.value_json for item in rows}, after=payload.values)
    db.commit()
    return success(request, {"values": payload.values, "version": current_version + 1})


@admin_router.get("/audit-logs", summary="只读审计日志")
def audit_logs(
    request: Request,
    actor_id: uuid.UUID | None = Query(default=None, alias="actorId"),
    module: str | None = None,
    action: str | None = None,
    target_type: str | None = Query(default=None, alias="targetType"),
    result: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("audit.read")),
    db: Session = Depends(get_db),
):
    statement = select(AuditLog).order_by(AuditLog.created_at.desc())
    for field, value in ((AuditLog.actor_id, actor_id), (AuditLog.module, module), (AuditLog.action, action), (AuditLog.target_type, target_type), (AuditLog.result, result)):
        if value is not None:
            statement = statement.where(field == value)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.offset(page.offset).limit(page.page_size)).all()
    data = [{"id": str(item.id), "actorId": str(item.actor_id) if item.actor_id else None, "module": item.module, "action": item.action, "targetType": item.target_type, "targetId": item.target_id, "result": item.result, "ip": item.ip, "requestId": item.request_id, "before": item.before_json, "after": item.after_json, "createdAt": item.created_at} for item in items]
    return paginated(request, data, page=page.page, page_size=page.page_size, total=total)


@notification_router.get("/notifications", summary="本人通知")
def notifications(
    request: Request,
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    page: PageParams = Depends(get_page_params),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    unread = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    items = db.scalars(statement.offset(page.offset).limit(page.page_size)).all()
    data = [{"id": str(item.id), "type": item.type, "title": item.title, "body": item.body, "targetType": item.target_type, "targetId": str(item.target_id) if item.target_id else None, "readAt": item.read_at, "createdAt": item.created_at} for item in items]
    response = paginated(
        request,
        data,
        page=page.page,
        page_size=page.page_size,
        total=total,
        meta={"unreadCount": unread},
    )
    response.headers["X-Unread-Count"] = str(unread)
    return response


@notification_router.post("/notifications/{notification_id}/read", status_code=204, summary="标记通知已读")
def read_notification(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if not item:
        raise AppError(404, "NOTIFICATION_NOT_FOUND", "通知不存在")
    item.read_at = item.read_at or utcnow()
    db.commit()
    return Response(status_code=204)


@notification_router.post("/notifications/read-all", status_code=204, summary="全部通知已读")
def read_all_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for item in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))):
        item.read_at = utcnow()
    db.commit()
    return Response(status_code=204)
