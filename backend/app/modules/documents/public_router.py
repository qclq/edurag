from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import AppError, success
from app.db.session import get_db
from app.integrations.storage import get_storage
from app.modules.conversations.models import Message, MessageCitation
from app.modules.conversations.schemas import CitationReportCreate
from app.modules.conversations.service import citation_payload
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion
from app.modules.documents.service import chunk_payload, document_payload, get_document_or_404
from app.modules.feedback.models import Feedback
from app.modules.operations.service import record_audit
from app.modules.users.models import User


router = APIRouter()


def _get_visible_citation(db: Session, citation_id, user: User) -> MessageCitation:
    citation = db.scalar(
        select(MessageCitation)
        .options(joinedload(MessageCitation.message).joinedload(Message.conversation))
        .where(MessageCitation.id == citation_id)
    )
    if not citation or citation.message.conversation.user_id != user.id:
        raise AppError(404, "CITATION_NOT_FOUND", "引用不存在")
    return citation


def _stream_document_version(
    db: Session,
    document: Document,
    version: DocumentVersion | None,
    user: User,
    request: Request,
    settings: Settings,
) -> StreamingResponse:
    if not version:
        raise AppError(409, "DOCUMENT_FILE_UNAVAILABLE", "文档当前没有可下载文件")
    stream = get_storage(settings).open(version.file.storage_key)
    record_audit(
        db,
        actor=user,
        module="document",
        action="download",
        target_type="document",
        target_id=document.id,
        request=request,
    )
    db.commit()
    encoded = quote(version.file.original_name)
    return StreamingResponse(
        stream,
        media_type=version.file.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "Content-Length": str(version.file.size_bytes),
        },
    )


@router.get("/citations/{citation_id}", summary="引用来源详情")
def get_citation(
    citation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    citation = _get_visible_citation(db, citation_id, user)
    document = get_document_or_404(db, citation.document_id, user, "preview")
    chunk = db.get(DocumentChunk, citation.chunk_id)
    siblings = db.scalars(
        select(MessageCitation).where(MessageCitation.message_id == citation.message_id).order_by(MessageCitation.citation_index)
    ).all()
    position = next(index for index, item in enumerate(siblings) if item.id == citation.id)
    return success(
        request,
        {
            "citation": citation_payload(citation, document_name=document.display_name),
            "document": document_payload(db, document),
            "chunk": chunk_payload(chunk, document) if chunk else None,
            "context": {"before": citation.context_before, "quote": citation.quote, "after": citation.context_after},
            "previousCitationId": str(siblings[position - 1].id) if position > 0 else None,
            "nextCitationId": str(siblings[position + 1].id) if position + 1 < len(siblings) else None,
            "permissions": {
                "canPreview": True,
                "canDownload": True,
            },
        },
    )


@router.get("/citations/{citation_id}/download", summary="下载问答引用的文档原件")
def download_cited_document(
    citation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    citation = _get_visible_citation(db, citation_id, user)
    document = db.scalar(
        select(Document).where(
            Document.id == citation.document_id,
            Document.deleted_at.is_(None),
        )
    )
    version = db.get(DocumentVersion, citation.document_version_id)
    if not document or not version or version.document_id != document.id:
        raise AppError(404, "CITATION_DOCUMENT_NOT_FOUND", "引用原文档已不可用")
    return _stream_document_version(db, document, version, user, request, settings)


@router.get("/documents/{document_id}/preview", summary="文档预览定位")
def preview_document(
    document_id: uuid.UUID,
    request: Request,
    page: int | None = Query(default=None, ge=1),
    chunk_id: uuid.UUID | None = Query(default=None, alias="chunkId"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user, "preview")
    version_id = document.current_version_id or next((item.id for item in document.versions if item.is_current), None)
    statement = select(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
    if chunk_id:
        statement = statement.where(DocumentChunk.id == chunk_id)
    elif page:
        statement = statement.where(DocumentChunk.page_start <= page, DocumentChunk.page_end >= page)
    chunks = db.scalars(statement.order_by(DocumentChunk.chunk_index).limit(50)).all()
    return success(request, {"document": document_payload(db, document), "location": {"page": page, "chunkId": str(chunk_id) if chunk_id else None}, "chunks": [chunk_payload(item, document) for item in chunks]})


@router.get("/documents/{document_id}/download", summary="下载文档原件")
def download_document(
    document_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    document = get_document_or_404(db, document_id, user, "download")
    version = next((item for item in document.versions if item.id == document.current_version_id), None) or next((item for item in document.versions if item.is_current), None)
    return _stream_document_version(db, document, version, user, request, settings)


@router.post("/citations/{citation_id}/reports", status_code=201, summary="报告引用问题")
def report_citation(
    citation_id: uuid.UUID,
    payload: CitationReportCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    citation = _get_visible_citation(db, citation_id, user)
    document = get_document_or_404(db, citation.document_id, user, "preview")
    feedback = Feedback(
        message_id=citation.message_id,
        citation_id=citation.id,
        conversation_id=citation.message.conversation_id,
        knowledge_base_id=document.knowledge_base_id,
        created_by=user.id,
        type=f"citation_{payload.type}",
        comment=payload.comment,
    )
    db.add(feedback)
    db.commit()
    return success(request, {"id": str(feedback.id), "type": feedback.type, "status": feedback.status, "createdAt": feedback.created_at}, status_code=201)
