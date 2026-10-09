from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import is_super_admin
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import utcnow
from app.integrations.storage import get_storage
from app.modules.documents.models import (
    Document,
    DocumentAclEntry,
    DocumentChunk,
    DocumentVersion,
    PendingUpload,
    StoredFile,
    Tag,
)
from app.modules.documents.schemas import DocumentMetadata, DocumentUpdate, IngestionCreate
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import can_access, get_kb_or_404
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import create_task
from app.modules.users.models import User


MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
}


def _validate_file(name: str, content: bytes, settings: Settings) -> tuple[str, str]:
    safe_name = Path(name or "").name
    if not safe_name or safe_name != name:
        raise AppError(400, "UPLOAD_FILENAME_INVALID", "文件名不合法")
    extension = Path(safe_name).suffix.lower().lstrip(".")
    if extension not in settings.upload_extensions:
        raise AppError(415, "UPLOAD_TYPE_UNSUPPORTED", "不支持该文件格式")
    if not content:
        raise AppError(400, "UPLOAD_EMPTY_FILE", "不能上传空文件")
    if len(content) > settings.upload_max_file_mb * 1024 * 1024:
        raise AppError(413, "UPLOAD_TOO_LARGE", "文件大小超过限制")
    if extension == "pdf" and not content.startswith(b"%PDF-"):
        raise AppError(415, "UPLOAD_CONTENT_MISMATCH", "文件内容与 PDF 扩展名不匹配")
    if extension == "docx" and not content.startswith(b"PK"):
        raise AppError(415, "UPLOAD_CONTENT_MISMATCH", "文件内容与 DOCX 扩展名不匹配")
    if extension in {"txt", "md"}:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(415, "UPLOAD_ENCODING_UNSUPPORTED", "文本文件必须使用 UTF-8 编码") from exc
    return safe_name, MIME_BY_EXTENSION[extension]


def create_upload(
    db: Session,
    user: User,
    *,
    client_file_id: str,
    file_name: str,
    content: bytes,
    content_type: str | None,
    settings: Settings,
) -> PendingUpload:
    safe_name, detected_type = _validate_file(file_name, content, settings)
    digest = hashlib.sha256(content).hexdigest()
    stored = db.scalar(select(StoredFile).where(StoredFile.sha256 == digest).order_by(StoredFile.created_at.desc()))
    if stored is None:
        key = f"{utcnow():%Y/%m}/{uuid.uuid4().hex}/{safe_name}"
        get_storage(settings).put(key, content, detected_type)
        stored = StoredFile(
            storage_key=key,
            original_name=safe_name,
            mime_type=detected_type,
            size_bytes=len(content),
            sha256=digest,
            scan_status="clean",
            scan_detail="基础格式检查通过；生产环境应接入恶意文件扫描服务",
            uploaded_by=user.id,
        )
        db.add(stored)
        db.flush()
    upload = PendingUpload(
        client_file_id=client_file_id,
        file_id=stored.id,
        uploaded_by=user.id,
        expires_at=utcnow() + timedelta(hours=settings.upload_ttl_hours),
    )
    db.add(upload)
    db.flush()
    upload._is_duplicate = db.scalar(
        select(func.count(DocumentVersion.id)).where(DocumentVersion.file_id == stored.id)
    ) > 0
    return upload


def upload_payload(upload: PendingUpload) -> dict[str, Any]:
    return {
        "uploadId": str(upload.id),
        "clientFileId": upload.client_file_id,
        "fileName": upload.file.original_name,
        "mimeType": upload.file.mime_type,
        "size": upload.file.size_bytes,
        "sha256": upload.file.sha256,
        "duplicate": bool(getattr(upload, "_is_duplicate", False)),
        "status": upload.status,
        "expiresAt": upload.expires_at,
    }


def get_upload(db: Session, upload_id, user: User, *, available: bool = True) -> PendingUpload:
    upload = db.scalar(
        select(PendingUpload).options(selectinload(PendingUpload.file)).where(PendingUpload.id == upload_id)
    )
    if not upload or upload.uploaded_by != user.id:
        raise AppError(404, "UPLOAD_NOT_FOUND", "暂存文件不存在")
    if available and (
        upload.status != "uploaded" or upload.consumed_at is not None or upload.expires_at.replace(tzinfo=UTC) <= utcnow()
    ):
        raise AppError(409, "UPLOAD_NOT_AVAILABLE", "暂存文件已使用或已过期")
    return upload


def _set_tags(db: Session, document: Document, names: list[str]) -> None:
    normalized = sorted({name.strip() for name in names if name.strip()})
    tags: list[Tag] = []
    for name in normalized:
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        tags.append(tag)
    document.tags = tags


def _create_document(db: Session, user: User, upload: PendingUpload, metadata: DocumentMetadata, config, draft: bool):
    kb = get_kb_or_404(db, metadata.knowledge_base_id)
    if not can_access(kb, user, "manage") and not is_super_admin(user):
        raise AppError(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在")
    document = Document(
        knowledge_base_id=kb.id,
        display_name=metadata.display_name,
        category=metadata.category,
        publish_department_id=metadata.publish_department_id,
        document_no=metadata.document_no,
        published_at=metadata.published_at,
        effective_at=metadata.effective_at,
        expires_at=metadata.expires_at,
        notes=metadata.notes,
        status="draft" if draft else "processing",
        uploaded_by=user.id,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_no=1,
        file_id=upload.file_id,
        process_status="draft" if draft else "pending",
        ocr_enabled=config.ocr_enabled,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )
    db.add(version)
    db.flush()
    _set_tags(db, document, metadata.tags)
    upload.status = "consumed"
    upload.consumed_at = utcnow()
    task = None
    if not draft:
        task, _ = create_task(
            db,
            type="document_ingestion",
            created_by=user.id,
            knowledge_base_id=kb.id,
            document_id=document.id,
            document_version_id=version.id,
            progress_text="等待解析文档",
        )
    return document, version, task


def create_ingestions(db: Session, user: User, payload: IngestionCreate):
    upload_ids = [item.upload_id for item in payload.items]
    if len(upload_ids) != len(set(upload_ids)):
        raise AppError(400, "UPLOAD_DUPLICATED_IN_REQUEST", "同一暂存文件不能重复提交")
    created = []
    for item in payload.items:
        upload = get_upload(db, item.upload_id, user)
        created.append(_create_document(db, user, upload, item.metadata, payload.processing_config, payload.save_as_draft))
    return created


def document_acl_allows(db: Session, document: Document, user: User, action: str) -> bool:
    if action != "download" and can_access(document.knowledge_base, user, "manage"):
        return True
    if not can_access(document.knowledge_base, user, action):
        return False
    entries = db.scalars(select(DocumentAclEntry).where(DocumentAclEntry.document_id == document.id)).all()
    if not entries:
        return True
    role_ids = {role.id for role in user.roles}
    column = {"search": "can_search", "preview": "can_preview", "download": "can_download"}.get(action, "can_preview")
    return any(
        getattr(entry, column)
        and (
            entry.is_public
            or entry.user_id == user.id
            or (entry.department_id and entry.department_id == user.department_id)
            or (entry.role_id and entry.role_id in role_ids)
        )
        for entry in entries
    )


def get_document_or_404(db: Session, document_id, user: User, action: str = "preview") -> Document:
    document = db.scalar(
        select(Document)
        .options(
            selectinload(Document.versions).selectinload(DocumentVersion.file),
            selectinload(Document.tags),
            selectinload(Document.knowledge_base).selectinload(KnowledgeBase.owners),
            selectinload(Document.knowledge_base).selectinload(KnowledgeBase.acl_entries),
        )
        .where(Document.id == document_id, Document.deleted_at.is_(None))
    )
    if not document or not document_acl_allows(db, document, user, action):
        raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在")
    return document


def document_payload(db: Session, document: Document, *, detailed: bool = False) -> dict[str, Any]:
    current = next((version for version in document.versions if version.id == document.current_version_id), None)
    if current is None:
        current = next((version for version in document.versions if version.is_current), None)
    chunk_count = (
        db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.document_version_id == current.id))
        if current
        else 0
    ) or 0
    data: dict[str, Any] = {
        "id": str(document.id),
        "knowledgeBase": {"id": str(document.knowledge_base.id), "name": document.knowledge_base.name},
        "displayName": document.display_name,
        "category": document.category,
        "publishDepartmentId": str(document.publish_department_id) if document.publish_department_id else None,
        "documentNo": document.document_no,
        "publishedAt": document.published_at,
        "effectiveAt": document.effective_at,
        "expiresAt": document.expires_at,
        "notes": document.notes,
        "status": document.status,
        "statusLabel": {
            "draft": "草稿",
            "processing": "处理中",
            "ready": "可用",
            "failed": "处理失败",
            "expired": "已过期",
            "disabled": "已停用",
        }.get(document.status, "未知状态"),
        "enabled": document.enabled,
        "tags": [tag.name for tag in document.tags],
        "chunkCount": chunk_count,
        "currentVersionId": str(current.id) if current else None,
        "version": document.row_version,
        "createdAt": document.created_at,
        "updatedAt": document.updated_at,
    }
    if detailed:
        data["versions"] = [
            {
                "id": str(version.id),
                "versionNo": version.version_no,
                "isCurrent": version.is_current,
                "processStatus": version.process_status,
                "processStage": version.process_stage,
                "fileName": version.file.original_name,
                "fileSize": version.file.size_bytes,
                "failureCode": version.failure_code,
                "failureMessage": version.failure_message,
                "processedAt": version.processed_at,
                "createdAt": version.created_at,
            }
            for version in sorted(document.versions, key=lambda item: item.version_no, reverse=True)
        ]
    return data


def update_document(db: Session, document: Document, payload: DocumentUpdate) -> None:
    if payload.version != document.row_version:
        raise AppError(409, "VERSION_CONFLICT", "文档已被其他用户修改，请刷新后重试")
    for field in (
        "display_name", "category", "publish_department_id", "document_no",
        "published_at", "effective_at", "expires_at", "notes",
    ):
        if field in payload.model_fields_set:
            setattr(document, field, getattr(payload, field))
    if payload.tags is not None:
        _set_tags(db, document, payload.tags)
    document.row_version += 1
    db.flush()


def chunk_payload(chunk: DocumentChunk, document: Document | None = None) -> dict[str, Any]:
    return {
        "id": str(chunk.id),
        "documentId": str(document.id) if document else None,
        "documentVersionId": str(chunk.document_version_id),
        "chunkIndex": chunk.chunk_index,
        "title": chunk.title,
        "section": chunk.section,
        "pageStart": chunk.page_start,
        "pageEnd": chunk.page_end,
        "content": chunk.content,
        "tokenCount": chunk.token_count,
        "vectorStatus": chunk.vector_status,
        "version": chunk.row_version,
        "createdAt": chunk.created_at,
        "updatedAt": chunk.updated_at,
    }
