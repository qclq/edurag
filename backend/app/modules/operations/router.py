from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_current_user, get_page_params, is_super_admin, require_permissions
from app.core.config import Settings, get_settings
from app.core.errors import AppError, paginated, success
from app.core.security import utcnow
from app.db.session import get_db
from app.modules.conversations.models import Conversation, MessageRating
from app.modules.documents.models import Document
from app.modules.feedback.models import Feedback, FeedbackEvent
from app.modules.feedback.schemas import FeedbackResolve
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import can_access, get_kb_or_404, list_accessible
from app.modules.operations.models import AuditLog, KnowledgeGap, Notification, QaArchiveSummary, QaRecord, SystemSetting
from app.modules.operations.retention import backfill_admin_records
from app.modules.operations.schemas import KnowledgeGapSupplement, ReportCreate, SettingsUpdate
from app.modules.operations.service import notify, record_audit
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import administrator_failure_payload, create_task, task_payload
from app.modules.users.models import User


admin_router = APIRouter()
notification_router = APIRouter()
ADMIN_ROLE_CODES = {"knowledge_admin", "user_admin", "super_admin"}


def _range_start(value: str):
    return utcnow() - timedelta(days={"today": 1, "7d": 7, "30d": 30}.get(value, 7))


def _is_admin_account(user: User) -> bool:
    return any(role.code in ADMIN_ROLE_CODES for role in user.roles)


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
    if backfill_admin_records(db):
        db.commit()
    qa_records = db.scalars(
        select(QaRecord)
        .options(selectinload(QaRecord.knowledge_bases))
        .where(QaRecord.created_at >= start)
    ).unique().all()
    summaries = db.scalars(
        select(QaArchiveSummary).where(QaArchiveSummary.summary_date >= start.date())
    ).all()
    if not super_admin:
        external_user_ids = set(db.scalars(select(User.id).where(User.user_type == "external")).all())
        qa_records = [
            item for item in qa_records
            if item.user_id not in external_user_ids
            and managed_ids.intersection(kb.id for kb in item.knowledge_bases)
        ]
        summaries = [item for item in summaries if item.knowledge_base_id in managed_ids]
    message_ids = {item.source_message_id for item in qa_records}
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
    for record in qa_records:
        key = record.created_at.date().isoformat()
        daily[key]["questions"] += 1
        if record.status == "completed":
            daily[key]["answered"] += 1
        elif record.status == "no_answer":
            daily[key]["noAnswer"] += 1
    for summary in summaries:
        key = summary.summary_date.isoformat()
        daily[key]["questions"] += summary.question_count
        daily[key]["answered"] += summary.answered_count
        daily[key]["noAnswer"] += summary.no_answer_count
    archived_question_count = sum(item.question_count for item in summaries)
    question_count = len(qa_records) + archived_question_count
    answered_count = sum(item.status == "completed" for item in qa_records) + sum(item.answered_count for item in summaries)
    helpful = sum(item.value == "helpful" for item in ratings) + sum(item.helpful_count for item in summaries)
    unhelpful = sum(item.value == "unhelpful" for item in ratings) + sum(item.unhelpful_count for item in summaries)
    return success(
        request,
        {
            "range": range,
            "metrics": {
                "conversationCount": len({item.source_conversation_id for item in qa_records}),
                "questionCount": question_count,
                "answerRate": round(answered_count / max(1, question_count), 4),
                "satisfactionRate": round(helpful / max(1, helpful + unhelpful), 4),
                "documentCount": len(documents),
                "failedTaskCount": sum(item.status == "failed" for item in tasks),
            },
            "trend": [{"date": key, **value} for key, value in sorted(daily.items())],
            "satisfaction": {"helpful": helpful, "unhelpful": unhelpful, "unrated": max(0, question_count - helpful - unhelpful)},
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
    if backfill_admin_records(db):
        db.commit()
    statement = (
        select(KnowledgeGap)
        .where(KnowledgeGap.last_occurred_at >= _range_start(range))
        .order_by(KnowledgeGap.status, KnowledgeGap.occurrence_count.desc(), KnowledgeGap.last_occurred_at.desc())
    )
    if status:
        statement = statement.where(KnowledgeGap.status == status)
    gaps = db.scalars(statement).all()
    kb_ids = {item.knowledge_base_id for item in gaps}
    knowledge_bases = {
        item.id: item
        for item in db.scalars(
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
            .where(KnowledgeBase.id.in_(kb_ids), KnowledgeBase.deleted_at.is_(None))
        ).unique().all()
    } if kb_ids else {}
    visible = [
        item for item in gaps
        if item.knowledge_base_id in knowledge_bases
        and (is_super_admin(user) or can_access(knowledge_bases[item.knowledge_base_id], user, "manage"))
    ]
    items = visible[page.offset : page.offset + page.page_size]
    return paginated(
        request,
        [
            {
                "id": str(item.id),
                "question": item.question,
                "count": item.occurrence_count,
                "firstOccurredAt": item.first_occurred_at,
                "lastOccurredAt": item.last_occurred_at,
                "status": item.status,
                "statusLabel": "已补充" if item.status == "supplemented" else "待补充",
                "knowledgeBaseId": str(item.knowledge_base_id),
                "knowledgeBaseName": knowledge_bases[item.knowledge_base_id].name,
                "supplementedAt": item.supplemented_at,
                "supplementNote": item.supplement_note,
                "suggestedAction": "补充相关文档或调整问题表达",
            }
            for item in items
        ],
        page=page.page,
        page_size=page.page_size,
        total=len(visible),
    )


@admin_router.post("/knowledge-gaps/{gap_id}/supplement", summary="标记知识缺口已补充")
def supplement_knowledge_gap(
    gap_id: uuid.UUID,
    payload: KnowledgeGapSupplement,
    request: Request,
    user: User = Depends(require_permissions("qa.analytics")),
    db: Session = Depends(get_db),
):
    gap = db.get(KnowledgeGap, gap_id)
    if not gap:
        raise AppError(404, "KNOWLEDGE_GAP_NOT_FOUND", "知识缺口不存在")
    kb = get_kb_or_404(db, gap.knowledge_base_id)
    if not is_super_admin(user):
        ensure_allowed = can_access(kb, user, "manage")
        if not ensure_allowed:
            raise AppError(404, "KNOWLEDGE_GAP_NOT_FOUND", "知识缺口不存在")
    gap.status = "supplemented"
    gap.supplemented_at = utcnow()
    gap.supplemented_by = user.id
    gap.supplement_note = payload.note
    record_audit(
        db,
        actor=user,
        module="qa",
        action="knowledge_gap.supplement",
        target_type="knowledge_gap",
        target_id=gap.id,
        request=request,
        after={"knowledgeBaseId": str(gap.knowledge_base_id), "note": payload.note},
    )
    db.commit()
    return success(
        request,
        {"id": str(gap.id), "status": gap.status, "statusLabel": "已补充", "supplementedAt": gap.supplemented_at},
    )


@admin_router.get("/anomalies", summary="异常任务分页")
def anomalies(
    request: Request,
    type: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("task.read")),
    db: Session = Depends(get_db),
):
    statement = select(ProcessingTask).where(
        ProcessingTask.status == "failed",
        ProcessingTask.knowledge_base_id.is_not(None),
    ).order_by(ProcessingTask.created_at.desc())
    if type:
        statement = statement.where(ProcessingTask.type == type)
    all_items = db.scalars(statement).all()
    if not is_super_admin(user):
        managed_ids = {kb.id for kb in list_accessible(db, user, "manage")}
        all_items = [item for item in all_items if item.knowledge_base_id in managed_ids]
    kb_ids = {item.knowledge_base_id for item in all_items if item.knowledge_base_id}
    knowledge_bases = {
        item.id: item
        for item in db.scalars(
            select(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids), KnowledgeBase.deleted_at.is_(None))
        ).all()
    } if kb_ids else {}
    all_items = [item for item in all_items if item.knowledge_base_id in knowledge_bases]
    total = len(all_items)
    items = all_items[page.offset : page.offset + page.page_size]
    return paginated(
        request,
        [
            administrator_failure_payload(item, knowledge_base_name=knowledge_bases[item.knowledge_base_id].name)
            for item in items
        ],
        page=page.page,
        page_size=page.page_size,
        total=total,
    )


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
    if backfill_admin_records(db):
        db.commit()
    statement = (
        select(QaRecord)
        .options(selectinload(QaRecord.knowledge_bases))
        .order_by(QaRecord.created_at.desc())
    )
    if status:
        statement = statement.where(QaRecord.status == status)
    if q:
        pattern = f"%{q}%"
        statement = statement.where(or_(QaRecord.question.ilike(pattern), QaRecord.answer.ilike(pattern)))
    super_admin = is_super_admin(user)
    external_user_ids = set(db.scalars(select(User.id).where(User.user_type == "external")).all())
    visible = [
        item
        for item in db.scalars(statement).unique().all()
        if (
            super_admin
            or (
                item.user_id not in external_user_ids
                and (
                    _is_admin_account(user)
                    or any(can_access(kb, user, "manage") for kb in item.knowledge_bases)
                )
            )
        )
    ]
    total = len(visible)
    items = visible[page.offset : page.offset + page.page_size]
    data = [
        {
            "id": str(item.id),
            "conversationId": str(item.source_conversation_id),
            "userId": str(item.user_id) if item.user_id else None,
            "question": item.question,
            "answerPreview": item.answer[:240],
            "status": item.status,
            "citationCount": item.citation_count,
            "modelName": item.model_name,
            "knowledgeBaseIds": [str(kb.id) for kb in item.knowledge_bases],
            "knowledgeBaseNames": [kb.name for kb in item.knowledge_bases],
            "createdAt": item.created_at,
            "completedAt": item.completed_at,
            "totalMs": item.total_ms,
        }
        for item in items
    ]
    return paginated(request, data, page=page.page, page_size=page.page_size, total=total)


@admin_router.get("/qa-summaries", summary="问答归档摘要")
def qa_summaries(
    request: Request,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("qa.read")),
    db: Session = Depends(get_db),
):
    summaries = db.scalars(
        select(QaArchiveSummary).order_by(QaArchiveSummary.summary_date.desc())
    ).all()
    knowledge_bases = {
        item.id: item
        for item in db.scalars(
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
            .where(KnowledgeBase.deleted_at.is_(None))
        ).unique().all()
    }
    visible = [
        item for item in summaries
        if item.knowledge_base_id in knowledge_bases
        and (is_super_admin(user) or _is_admin_account(user) or can_access(knowledge_bases[item.knowledge_base_id], user, "manage"))
    ]
    items = visible[page.offset : page.offset + page.page_size]
    return paginated(
        request,
        [
            {
                "id": str(item.id),
                "date": item.summary_date,
                "knowledgeBaseId": str(item.knowledge_base_id),
                "knowledgeBaseName": knowledge_bases[item.knowledge_base_id].name,
                "questionCount": item.question_count,
                "answeredCount": item.answered_count,
                "noAnswerCount": item.no_answer_count,
                "failedCount": item.failed_count,
                "helpfulCount": item.helpful_count,
                "unhelpfulCount": item.unhelpful_count,
                "averageResponseMs": round(item.response_ms_total / item.response_ms_samples) if item.response_ms_samples else None,
                "resolvedFeedbackCount": item.resolved_feedback_count,
                "supplementedGapCount": item.supplemented_gap_count,
                "topQuestions": item.top_questions,
                "summary": item.summary_text,
            }
            for item in items
        ],
        page=page.page,
        page_size=page.page_size,
        total=len(visible),
    )


@admin_router.get("/qa-records/{record_id}", summary="完整问答审计详情")
def qa_record_detail(
    record_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("qa.content.read")),
    db: Session = Depends(get_db),
):
    record = db.scalar(
        select(QaRecord)
        .options(selectinload(QaRecord.knowledge_bases))
        .where(QaRecord.id == record_id)
    )
    external_owner = bool(
        record
        and db.scalar(select(User.user_type).where(User.id == record.user_id)) == "external"
    )
    if not record or (
        external_owner
        and not is_super_admin(user)
    ) or (
        not external_owner
        and not _is_admin_account(user)
        and not any(can_access(kb, user, "manage") for kb in record.knowledge_bases)
    ):
        raise AppError(404, "QA_RECORD_NOT_FOUND", "问答记录不存在")
    record_audit(db, actor=user, module="qa", action="content.read", target_type="qa_record", target_id=record.id, request=request)
    db.commit()
    return success(
        request,
        {
            "question": {"content": record.question, "createdAt": record.created_at},
            "answer": {
                "content": record.answer,
                "status": record.status,
                "modelName": record.model_name,
                "timing": {"totalMs": record.total_ms},
                "citationCount": record.citation_count,
                "completedAt": record.completed_at,
            },
            "knowledgeBases": [{"id": str(kb.id), "name": kb.name} for kb in record.knowledge_bases],
            "events": [],
        },
    )


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
    creator_type = db.scalar(select(User.user_type).where(User.id == item.created_by))
    if creator_type == "external":
        return is_super_admin(user)
    if _is_admin_account(user):
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
