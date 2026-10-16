from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import Settings
from app.core.security import utcnow
from app.modules.conversations.models import Conversation, Message, MessageRating
from app.modules.feedback.models import Feedback
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.operations.models import (
    KnowledgeGap,
    KnowledgeGapOccurrence,
    QaArchiveMarker,
    QaArchiveSummary,
    QaRecord,
)
from app.modules.tasks.models import ProcessingTask
from app.modules.users.models import User  # noqa: F401 - registers ORM relationships for standalone workers


TERMINAL_QA_STATUSES = {"completed", "no_answer", "failed", "stopped"}


def _question_fingerprint(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip().lower())[:500]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def record_knowledge_gap(
    db: Session,
    *,
    source_message_id: uuid.UUID,
    question: str,
    occurred_at: datetime,
    knowledge_bases: list[KnowledgeBase],
) -> None:
    fingerprint = _question_fingerprint(question)
    for kb in knowledge_bases:
        if kb.deleted_at is not None:
            continue
        gap = db.scalar(
            select(KnowledgeGap)
            .options(selectinload(KnowledgeGap.occurrences))
            .where(
                KnowledgeGap.knowledge_base_id == kb.id,
                KnowledgeGap.question_fingerprint == fingerprint,
            )
        )
        if gap is None:
            gap = KnowledgeGap(
                knowledge_base_id=kb.id,
                question=question.strip() or "未知问题",
                question_fingerprint=fingerprint,
                occurrence_count=0,
                first_occurred_at=occurred_at,
                last_occurred_at=occurred_at,
            )
            db.add(gap)
            db.flush()
        if any(item.source_message_id == source_message_id for item in gap.occurrences):
            continue
        gap.occurrences.append(
            KnowledgeGapOccurrence(source_message_id=source_message_id, occurred_at=occurred_at)
        )
        gap.occurrence_count += 1
        gap.last_occurred_at = max(gap.last_occurred_at, occurred_at)
        gap.first_occurred_at = min(gap.first_occurred_at, occurred_at)
        if gap.status == "supplemented":
            # A repeated miss means the supplementation did not fully close the gap.
            gap.status = "open"
            gap.supplemented_at = None
            gap.supplemented_by = None
            gap.supplement_note = None


def capture_qa_record(
    db: Session,
    *,
    assistant: Message,
    question: Message,
    conversation: Conversation,
) -> QaRecord | None:
    if db.get(QaArchiveMarker, assistant.id):
        return None
    existing = db.scalar(select(QaRecord).where(QaRecord.source_message_id == assistant.id))
    if existing:
        return existing
    knowledge_bases = [kb for kb in conversation.knowledge_bases if kb.deleted_at is None]
    record = QaRecord(
        source_message_id=assistant.id,
        source_conversation_id=conversation.id,
        user_id=conversation.user_id,
        question=question.content,
        answer=assistant.content or "",
        status=assistant.status,
        citation_count=len(assistant.citations),
        model_name=assistant.model_name,
        total_ms=assistant.total_ms,
        completed_at=assistant.completed_at,
        created_at=assistant.created_at,
        knowledge_bases=knowledge_bases,
    )
    db.add(record)
    db.flush()
    if assistant.status == "no_answer":
        record_knowledge_gap(
            db,
            source_message_id=assistant.id,
            question=question.content,
            occurred_at=assistant.completed_at or assistant.created_at,
            knowledge_bases=knowledge_bases,
        )
    return record


def backfill_admin_records(db: Session, *, limit: int = 500) -> int:
    existing_ids = select(QaRecord.source_message_id)
    archived_ids = select(QaArchiveMarker.source_message_id)
    messages = db.scalars(
        select(Message)
        .options(
            selectinload(Message.citations),
            joinedload(Message.conversation).selectinload(Conversation.knowledge_bases),
        )
        .where(
            Message.role == "assistant",
            Message.status.in_(TERMINAL_QA_STATUSES),
            Message.id.not_in(existing_ids),
            Message.id.not_in(archived_ids),
        )
        .order_by(Message.created_at)
        .limit(limit)
    ).unique().all()
    for assistant in messages:
        question = db.get(Message, assistant.parent_message_id)
        if question and assistant.conversation:
            capture_qa_record(
                db,
                assistant=assistant,
                question=question,
                conversation=assistant.conversation,
            )
    if messages:
        db.flush()
    return len(messages)


def _get_summary(db: Session, kb_id: uuid.UUID, summary_date) -> QaArchiveSummary:
    summary = db.scalar(
        select(QaArchiveSummary).where(
            QaArchiveSummary.knowledge_base_id == kb_id,
            QaArchiveSummary.summary_date == summary_date,
        )
    )
    if summary is None:
        summary = QaArchiveSummary(knowledge_base_id=kb_id, summary_date=summary_date)
        db.add(summary)
        db.flush()
    return summary


def _merge_top_question(summary: QaArchiveSummary, question: str) -> None:
    counts = Counter(
        {
            str(item.get("question", "")): int(item.get("count", 0))
            for item in (summary.top_questions or [])
            if item.get("question")
        }
    )
    counts[re.sub(r"\s+", " ", question.strip())[:120] or "未命名问题"] += 1
    summary.top_questions = [
        {"question": value, "count": count}
        for value, count in counts.most_common(5)
    ]


def _refresh_summary_text(summary: QaArchiveSummary) -> None:
    average = round(summary.response_ms_total / summary.response_ms_samples) if summary.response_ms_samples else 0
    parts = [
        f"{summary.summary_date.isoformat()} 共归档 {summary.question_count} 条问答记录",
        f"其中正常回答 {summary.answered_count} 条、未找到可靠依据 {summary.no_answer_count} 条、处理失败 {summary.failed_count} 条",
    ]
    if average:
        parts.append(f"平均响应时间约 {average} 毫秒")
    if summary.resolved_feedback_count:
        parts.append(f"已归档 {summary.resolved_feedback_count} 条已解决反馈")
    if summary.supplemented_gap_count:
        parts.append(f"已完成 {summary.supplemented_gap_count} 项知识缺口补充")
    summary.summary_text = "；".join(parts) + "。"


def run_admin_record_retention(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or utcnow()
    cutoff = now - timedelta(hours=settings.admin_record_retention_hours)
    deleted_kb_ids = db.scalars(
        select(KnowledgeBase.id).where(KnowledgeBase.deleted_at.is_not(None))
    ).all()
    purged_deleted_kb_records = 0
    for kb_id in deleted_kb_ids:
        cleanup = purge_knowledge_base_operations(db, kb_id)
        purged_deleted_kb_records += sum(cleanup.values())
    # Keep draining historical rows in bounded batches so the periodic job remains predictable.
    backfilled = backfill_admin_records(db, limit=settings.admin_record_cleanup_batch_size)
    records = db.scalars(
        select(QaRecord)
        .options(selectinload(QaRecord.knowledge_bases))
        .where(QaRecord.created_at < cutoff)
        .order_by(QaRecord.created_at)
        .limit(settings.admin_record_cleanup_batch_size)
    ).unique().all()
    message_ids = [item.source_message_id for item in records]
    feedbacks_by_message: dict[uuid.UUID, list[Feedback]] = {}
    if message_ids:
        for item in db.scalars(select(Feedback).where(Feedback.message_id.in_(message_ids))).all():
            feedbacks_by_message.setdefault(item.message_id, []).append(item)
    ratings = {}
    if message_ids:
        for item in db.scalars(select(MessageRating).where(MessageRating.message_id.in_(message_ids))).all():
            ratings.setdefault(item.message_id, []).append(item)

    touched: dict[tuple[uuid.UUID, object], QaArchiveSummary] = {}
    archived_records = 0
    for record in records:
        linked_feedback = feedbacks_by_message.get(record.source_message_id, [])
        if any(item.status != "resolved" or not item.resolved_at or item.resolved_at >= cutoff for item in linked_feedback):
            continue
        # Archive each interaction once so cross-knowledge-base questions do not inflate global totals.
        primary_kb = min(record.knowledge_bases, key=lambda item: str(item.id), default=None)
        for kb in [primary_kb] if primary_kb else []:
            summary = _get_summary(db, kb.id, record.created_at.date())
            touched[(kb.id, record.created_at.date())] = summary
            summary.question_count += 1
            summary.answered_count += int(record.status == "completed")
            summary.no_answer_count += int(record.status == "no_answer")
            summary.failed_count += int(record.status in {"failed", "stopped"})
            if record.total_ms is not None:
                summary.response_ms_total += record.total_ms
                summary.response_ms_samples += 1
            for rating in ratings.get(record.source_message_id, []):
                summary.helpful_count += int(rating.value == "helpful")
                summary.unhelpful_count += int(rating.value == "unhelpful")
            _merge_top_question(summary, record.question)
        db.add(QaArchiveMarker(source_message_id=record.source_message_id, archived_at=now))
        db.delete(record)
        archived_records += 1

    resolved_feedbacks = db.scalars(
        select(Feedback).where(
            Feedback.status == "resolved",
            Feedback.resolved_at.is_not(None),
            Feedback.resolved_at < cutoff,
        )
        .order_by(Feedback.resolved_at)
        .limit(settings.admin_record_cleanup_batch_size)
    ).all()
    archived_feedbacks = 0
    for feedback in resolved_feedbacks:
        if not feedback.knowledge_base_id:
            continue
        summary_date = feedback.resolved_at.date()
        summary = _get_summary(db, feedback.knowledge_base_id, summary_date)
        touched[(feedback.knowledge_base_id, summary_date)] = summary
        summary.resolved_feedback_count += 1
        types = dict(summary.feedback_types or {})
        types[feedback.type] = int(types.get(feedback.type, 0)) + 1
        summary.feedback_types = types
        db.delete(feedback)
        archived_feedbacks += 1

    supplemented_gaps = db.scalars(
        select(KnowledgeGap)
        .options(selectinload(KnowledgeGap.occurrences))
        .where(
            KnowledgeGap.status == "supplemented",
            KnowledgeGap.supplemented_at.is_not(None),
            KnowledgeGap.supplemented_at < cutoff,
        )
        .order_by(KnowledgeGap.supplemented_at)
        .limit(settings.admin_record_cleanup_batch_size)
    ).all()
    archived_gaps = 0
    for gap in supplemented_gaps:
        summary_date = gap.supplemented_at.date()
        summary = _get_summary(db, gap.knowledge_base_id, summary_date)
        touched[(gap.knowledge_base_id, summary_date)] = summary
        summary.supplemented_gap_count += 1
        db.delete(gap)
        archived_gaps += 1

    for summary in touched.values():
        _refresh_summary_text(summary)
    db.commit()
    return {
        "backfilled": backfilled,
        "qaRecordsArchived": archived_records,
        "feedbacksArchived": archived_feedbacks,
        "knowledgeGapsArchived": archived_gaps,
        "deletedKnowledgeBaseRecordsPurged": purged_deleted_kb_records,
    }


def purge_knowledge_base_operations(db: Session, kb_id: uuid.UUID) -> dict[str, int]:
    qa_records = db.scalars(
        select(QaRecord)
        .options(selectinload(QaRecord.knowledge_bases))
        .where(QaRecord.knowledge_bases.any(KnowledgeBase.id == kb_id))
    ).unique().all()
    summaries = db.scalars(
        select(QaArchiveSummary).where(QaArchiveSummary.knowledge_base_id == kb_id)
    ).all()
    gaps = db.scalars(
        select(KnowledgeGap)
        .options(selectinload(KnowledgeGap.occurrences))
        .where(KnowledgeGap.knowledge_base_id == kb_id)
    ).all()
    tasks = db.scalars(
        select(ProcessingTask)
        .options(selectinload(ProcessingTask.events), selectinload(ProcessingTask.items))
        .where(ProcessingTask.knowledge_base_id == kb_id)
    ).unique().all()
    for collection in (qa_records, summaries, gaps, tasks):
        for item in collection:
            db.delete(item)
    db.flush()
    return {
        "qaRecords": len(qa_records),
        "summaries": len(summaries),
        "knowledgeGaps": len(gaps),
        "tasks": len(tasks),
    }
