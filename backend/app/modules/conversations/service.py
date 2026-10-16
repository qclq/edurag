from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import utcnow
from app.integrations.llm import LlmProviderError, plan_retrieval_queries, stream_answer
from app.modules.conversations.models import (
    Conversation,
    Message,
    MessageCitation,
    MessageEvent,
    MessageRating,
)
from app.modules.conversations.schemas import MessageCreate, RatingUpdate
from app.modules.feedback.models import Feedback
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import can_access, get_kb_or_404, list_accessible
from app.modules.operations.retention import capture_qa_record
from app.modules.rag.service import retrieve_queries
from app.modules.users.models import User


TERMINAL_MESSAGE_STATUSES = {"completed", "no_answer", "stopped", "failed"}


def citation_payload(citation: MessageCitation, *, document_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(citation.id),
        "index": citation.citation_index,
        "documentId": str(citation.document_id),
        "documentVersionId": str(citation.document_version_id),
        "chunkId": str(citation.chunk_id),
        "documentName": document_name,
        "quote": citation.quote,
        "pageStart": citation.page_start,
        "pageEnd": citation.page_end,
        "section": citation.section,
        "similarityScore": citation.similarity_score,
        "rerankScore": citation.rerank_score,
    }


def message_payload(message: Message, *, include_citations: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(message.id),
        "conversationId": str(message.conversation_id),
        "parentMessageId": str(message.parent_message_id) if message.parent_message_id else None,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "statusLabel": {
            "submitted": "已提交", "retrieving": "检索中", "generating": "生成中",
            "completed": "已完成", "no_answer": "无可靠答案", "stopped": "已停止", "failed": "生成失败",
        }.get(message.status, "未知状态"),
        "generationVersion": message.generation_version,
        "error": {"code": message.error_code, "message": message.error_message} if message.error_code else None,
        "timing": {
            "retrievalMs": message.retrieval_ms,
            "firstTokenMs": message.first_token_ms,
            "totalMs": message.total_ms,
        },
        "modelName": message.model_name,
        "createdAt": message.created_at,
        "completedAt": message.completed_at,
    }
    if include_citations:
        data["citations"] = [citation_payload(item) for item in message.citations]
    return data


def conversation_payload(conversation: Conversation, *, include_messages: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(conversation.id),
        "title": conversation.title,
        "status": conversation.status,
        "favorite": conversation.is_favorite,
        "messageCount": conversation.message_count,
        "knowledgeBases": [
            {"id": str(kb.id), "name": kb.name, "code": kb.code} for kb in conversation.knowledge_bases
        ],
        "lastMessageAt": conversation.last_message_at,
        "createdAt": conversation.created_at,
        "updatedAt": conversation.updated_at,
    }
    if include_messages:
        result["messages"] = [message_payload(item) for item in conversation.messages]
    return result


def get_conversation_or_404(db: Session, conversation_id, user: User) -> Conversation:
    conversation = db.scalar(
        select(Conversation)
        .options(
            selectinload(Conversation.knowledge_bases),
            selectinload(Conversation.messages).selectinload(Message.citations),
        )
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.deleted_at.is_(None),
        )
    )
    if not conversation:
        raise AppError(404, "CONVERSATION_NOT_FOUND", "会话不存在")
    return conversation


def create_conversation(db: Session, user: User, knowledge_base_ids: list | None, title: str | None) -> Conversation:
    unique_ids = list(dict.fromkeys(knowledge_base_ids or []))
    kbs = []
    if unique_ids:
        for kb_id in unique_ids:
            kb = get_kb_or_404(db, kb_id)
            if not can_access(kb, user, "search") or kb.status != "enabled":
                raise AppError(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在")
            kbs.append(kb)
    else:
        kbs = list_accessible(db, user, "search")
    if not kbs:
        raise AppError(409, "NO_ACCESSIBLE_KNOWLEDGE_BASE", "当前账号没有可检索的知识库")
    conversation = Conversation(user_id=user.id, title=title or "新对话", knowledge_bases=kbs)
    db.add(conversation)
    db.flush()
    return conversation


def append_message_event(db: Session, message: Message, event_type: str, payload: dict[str, Any]) -> MessageEvent:
    sequence = db.scalar(select(func.max(MessageEvent.sequence)).where(MessageEvent.message_id == message.id)) or 0
    event = MessageEvent(
        message_id=message.id,
        sequence=sequence + 1,
        event_type=event_type,
        payload=jsonable_encoder(payload),
    )
    db.add(event)
    db.flush()
    return event


def submit_message(db: Session, conversation: Conversation, payload: MessageCreate) -> tuple[Message, Message, bool]:
    existing = db.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.idempotency_key == payload.client_request_id,
            Message.role == "user",
        )
    )
    if existing:
        assistant = db.scalar(
            select(Message)
            .options(selectinload(Message.citations))
            .where(Message.parent_message_id == existing.id, Message.role == "assistant")
            .order_by(Message.generation_version.desc())
        )
        return existing, assistant, False
    if payload.knowledge_base_ids is not None:
        requested = set(payload.knowledge_base_ids)
        current = {kb.id for kb in conversation.knowledge_bases}
        if not requested.issubset(current):
            raise AppError(400, "CONVERSATION_SCOPE_INVALID", "消息检索范围必须是会话知识库范围的子集")
    user_message = Message(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        status="completed",
        completed_at=utcnow(),
        idempotency_key=payload.client_request_id,
    )
    db.add(user_message)
    db.flush()
    assistant = Message(
        conversation_id=conversation.id,
        parent_message_id=user_message.id,
        role="assistant",
        content="",
        status="submitted",
        idempotency_key=f"{payload.client_request_id}:assistant",
    )
    db.add(assistant)
    conversation.message_count += 2
    conversation.last_message_at = utcnow()
    if conversation.title == "新对话":
        conversation.title = payload.content[:30] + ("…" if len(payload.content) > 30 else "")
    db.flush()
    append_message_event(
        db,
        assistant,
        "stage",
        {"messageId": str(assistant.id), "status": "submitted", "stage": "understanding", "label": "正在理解问题"},
    )
    return user_message, assistant, True


def get_message_or_404(db: Session, message_id, user: User) -> Message:
    message = db.scalar(
        select(Message)
        .options(selectinload(Message.citations), selectinload(Message.events), joinedload(Message.conversation))
        .where(Message.id == message_id)
    )
    if not message or message.conversation.user_id != user.id or message.conversation.deleted_at is not None:
        raise AppError(404, "MESSAGE_NOT_FOUND", "消息不存在")
    return message


def generate_message(db: Session, assistant: Message, user: User, settings: Settings) -> None:
    started = time.perf_counter()
    try:
        question = db.get(Message, assistant.parent_message_id)
        conversation = db.scalar(
            select(Conversation).options(selectinload(Conversation.knowledge_bases)).where(Conversation.id == assistant.conversation_id)
        )
        if not question or not conversation:
            raise RuntimeError("关联问题或会话不存在")
        if assistant.status == "stopped":
            return
        assistant.status = "retrieving"
        append_message_event(
            db, assistant, "stage",
            {"messageId": str(assistant.id), "status": "retrieving", "stage": "planning", "label": "正在拆解检索问题"},
        )
        db.commit()

        planning_started = time.perf_counter()
        planner_fallback = False
        try:
            retrieval_queries = plan_retrieval_queries(question.content, settings)
        except LlmProviderError:
            retrieval_queries = [question.content]
            planner_fallback = True
        planning_ms = int((time.perf_counter() - planning_started) * 1000)
        accessible_kbs = list_accessible(db, user, "search")
        if not accessible_kbs:
            raise AppError(409, "NO_ACCESSIBLE_KNOWLEDGE_BASE", "当前账号没有可检索的知识库")
        conversation.knowledge_bases = accessible_kbs
        append_message_event(
            db, assistant, "stage",
            {
                "messageId": str(assistant.id),
                "status": "retrieving",
                "stage": "retrieval",
                "label": f"正在执行 {len(retrieval_queries)} 条检索查询",
                "queries": retrieval_queries,
            },
        )
        db.commit()
        chunks, summary = retrieve_queries(
            db,
            user,
            retrieval_queries,
            settings,
            knowledge_base_ids=None,
            top_k=max(8, max((kb.top_k for kb in accessible_kbs), default=5)),
            # Qwen dense scores around 0.35 can still be strong matches after
            # query decomposition. Keep stricter KB thresholds for diagnostics,
            # but cap the automatic cross-KB gate so useful evidence reaches the
            # answer model, which still has to ground every conclusion in citations.
            min_score=min(0.35, min((kb.similarity_threshold for kb in accessible_kbs), default=0.35)),
        )
        summary["planningMs"] = planning_ms
        summary["plannerFallback"] = planner_fallback
        assistant.retrieval_ms = summary["retrievalMs"]
        assistant.status = "generating"
        append_message_event(
            db, assistant, "stage",
            {"messageId": str(assistant.id), "status": "generating", "stage": "generation", "label": "正在组织回答", "retrieval": summary},
        )
        db.commit()
        answer_parts: list[str] = []
        assistant.model_name = settings.deepseek_model if settings.llm_provider == "deepseek" else "extractive"
        for delta in stream_answer(question.content, chunks, settings):
            db.refresh(assistant)
            if assistant.status == "stopped":
                return
            if assistant.first_token_ms is None:
                assistant.first_token_ms = int((time.perf_counter() - started) * 1000)
            answer_parts.append(delta)
            assistant.content = "".join(answer_parts)
            append_message_event(
                db, assistant, "delta",
                {"messageId": str(assistant.id), "text": delta},
            )
            db.commit()
        answer = "".join(answer_parts)
        if not answer:
            raise RuntimeError("模型未返回回答内容")
        for index, item in enumerate(chunks, 1):
            citation = MessageCitation(
                message_id=assistant.id,
                citation_index=index,
                document_id=uuid.UUID(item["documentId"]),
                document_version_id=uuid.UUID(item["documentVersionId"]),
                chunk_id=uuid.UUID(item["chunkId"]),
                quote=str(item["content"])[:1000],
                page_start=item["pageStart"],
                page_end=item["pageEnd"],
                section=item["section"],
                similarity_score=item["score"],
                rerank_score=item["score"],
            )
            db.add(citation)
            db.flush()
            append_message_event(db, assistant, "citation", {"messageId": str(assistant.id), "citation": citation_payload(citation, document_name=item["documentName"])})
        assistant.status = "completed" if chunks else "no_answer"
        assistant.total_ms = int((time.perf_counter() - started) * 1000)
        assistant.completed_at = utcnow()
        append_message_event(
            db,
            assistant,
            "suggestions",
            {"items": ["这项规定适用于哪些人？", "需要准备哪些材料？", "相关文件何时生效？"]},
        )
        db.flush()
        capture_qa_record(db, assistant=assistant, question=question, conversation=conversation)
        append_message_event(db, assistant, "completed", {"message": message_payload(assistant), "retrieval": summary})
        db.commit()
    except Exception as exc:
        db.rollback()
        assistant = db.get(Message, assistant.id)
        if not assistant or assistant.status == "stopped":
            return
        code = getattr(exc, "code", "RAG_GENERATION_FAILED")
        assistant.status = "failed"
        assistant.error_code = code
        assistant.error_message = str(exc)[:1000] or "回答生成失败"
        assistant.total_ms = int((time.perf_counter() - started) * 1000)
        assistant.completed_at = utcnow()
        append_message_event(
            db, assistant, "error",
            {"messageId": str(assistant.id), "code": code, "message": assistant.error_message, "retryable": True},
        )
        question = db.get(Message, assistant.parent_message_id)
        conversation = db.scalar(
            select(Conversation)
            .options(selectinload(Conversation.knowledge_bases))
            .where(Conversation.id == assistant.conversation_id)
        )
        if question and conversation:
            capture_qa_record(db, assistant=assistant, question=question, conversation=conversation)
        db.commit()


def update_rating(db: Session, message: Message, user: User, payload: RatingUpdate) -> tuple[MessageRating | None, Feedback | None]:
    rating = db.scalar(
        select(MessageRating).where(MessageRating.message_id == message.id, MessageRating.user_id == user.id)
    )
    if payload.value == "none":
        if rating:
            db.delete(rating)
        db.flush()
        return None, None
    if rating is None:
        rating = MessageRating(message_id=message.id, user_id=user.id, value=payload.value)
        db.add(rating)
    rating.value = payload.value
    rating.reason = payload.reason
    rating.comment = payload.comment
    feedback = None
    if payload.value == "unhelpful":
        feedback = db.scalar(
            select(Feedback).where(
                Feedback.message_id == message.id,
                Feedback.created_by == user.id,
                Feedback.type == "unhelpful",
                Feedback.status.in_(["pending", "processing"]),
            )
        )
        if feedback is None:
            feedback = Feedback(
                message_id=message.id,
                conversation_id=message.conversation_id,
                knowledge_base_id=(
                    message.conversation.knowledge_bases[0].id
                    if message.conversation.knowledge_bases
                    else None
                ),
                created_by=user.id,
                type="unhelpful",
                comment=payload.comment or payload.reason,
            )
            db.add(feedback)
    db.flush()
    return rating, feedback
