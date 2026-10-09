from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import is_super_admin
from app.core.errors import AppError
from app.core.security import utcnow
from app.modules.knowledge_bases.models import KnowledgeBase, KnowledgeBaseAclEntry
from app.modules.knowledge_bases.schemas import AclEntryInput, KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.modules.users.models import User


ACTION_COLUMN = {
    "search": "can_search",
    "preview": "can_preview",
    "download": "can_download",
    "manage": "can_manage",
}


def _subject_matches(entry: KnowledgeBaseAclEntry, user: User) -> bool:
    role_ids = {role.id for role in user.roles}
    return bool(
        entry.is_public
        or entry.user_id == user.id
        or (entry.department_id and entry.department_id == user.department_id)
        or (entry.role_id and entry.role_id in role_ids)
    )


def can_access(kb: KnowledgeBase, user: User, action: str) -> bool:
    if action != "download" and (
        is_super_admin(user) or kb.created_by == user.id or any(owner.id == user.id for owner in kb.owners)
    ):
        return True
    column = ACTION_COLUMN[action]
    return any(_subject_matches(entry, user) and bool(getattr(entry, column)) for entry in kb.acl_entries)


def ensure_access(kb: KnowledgeBase, user: User, action: str) -> None:
    if kb.deleted_at is not None or not can_access(kb, user, action):
        raise AppError(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在")


def acl_payload(entry: KnowledgeBaseAclEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "isPublic": entry.is_public,
        "roleId": str(entry.role_id) if entry.role_id else None,
        "departmentId": str(entry.department_id) if entry.department_id else None,
        "userId": str(entry.user_id) if entry.user_id else None,
        "canSearch": entry.can_search,
        "canPreview": entry.can_preview,
        "canDownload": entry.can_download,
        "canManage": entry.can_manage,
    }


def knowledge_base_payload(db: Session, kb: KnowledgeBase, *, detailed: bool = False) -> dict[str, Any]:
    from app.modules.documents.models import Document

    document_count = db.scalar(
        select(func.count(Document.id)).where(Document.knowledge_base_id == kb.id, Document.deleted_at.is_(None))
    ) or 0
    ready_count = db.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == kb.id,
            Document.deleted_at.is_(None),
            Document.status == "ready",
        )
    ) or 0
    result: dict[str, Any] = {
        "id": str(kb.id),
        "code": kb.code,
        "name": kb.name,
        "description": kb.description,
        "departmentId": str(kb.department_id) if kb.department_id else None,
        "businessCategory": kb.business_category,
        "selectable": kb.selectable,
        "status": kb.status,
        "statusLabel": {"enabled": "已启用", "disabled": "已停用"}.get(kb.status, "未知状态"),
        "ownerIds": [str(owner.id) for owner in kb.owners],
        "documentCount": document_count,
        "readyDocumentCount": ready_count,
        "version": kb.row_version,
        "createdBy": str(kb.created_by),
        "createdAt": kb.created_at,
        "updatedAt": kb.updated_at,
    }
    if detailed:
        result.update(
            {
                "acl": [acl_payload(entry) for entry in kb.acl_entries],
                "retrieval": {
                    "chunkSize": kb.chunk_size,
                    "chunkOverlap": kb.chunk_overlap,
                    "topK": kb.top_k,
                    "similarityThreshold": kb.similarity_threshold,
                    "rerankEnabled": kb.rerank_enabled,
                    "hybridSearchEnabled": kb.hybrid_search_enabled,
                    "promptInstruction": kb.prompt_instruction,
                },
            }
        )
    return result


def get_kb_or_404(db: Session, kb_id) -> KnowledgeBase:
    kb = db.scalar(
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
        .where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None))
    )
    if not kb:
        raise AppError(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在")
    return kb


def list_accessible(db: Session, user: User, action: str = "search", *, selectable: bool | None = None) -> list[KnowledgeBase]:
    query = (
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
        .where(KnowledgeBase.deleted_at.is_(None))
        .order_by(KnowledgeBase.updated_at.desc())
    )
    if action == "search":
        query = query.where(KnowledgeBase.status == "enabled")
    if selectable is not None:
        query = query.where(KnowledgeBase.selectable == selectable)
    return [kb for kb in db.scalars(query).unique().all() if can_access(kb, user, action)]


def _set_owners(db: Session, kb: KnowledgeBase, owner_ids: list[uuid.UUID]) -> None:
    owners = db.scalars(select(User).where(User.id.in_(owner_ids), User.status == "active")).all() if owner_ids else []
    if len(owners) != len(set(owner_ids)):
        raise AppError(400, "KB_OWNER_INVALID", "部分负责人不存在或不可用")
    kb.owners = list(owners)


def _new_acl(kb: KnowledgeBase, item: AclEntryInput) -> KnowledgeBaseAclEntry:
    return KnowledgeBaseAclEntry(knowledge_base=kb, **item.model_dump())


def create_knowledge_base(db: Session, user: User, payload: KnowledgeBaseCreate) -> KnowledgeBase:
    retrieval = payload.retrieval
    kb = KnowledgeBase(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        department_id=payload.department_id,
        business_category=payload.business_category,
        selectable=payload.selectable,
        created_by=user.id,
        chunk_size=retrieval.chunk_size,
        chunk_overlap=retrieval.chunk_overlap,
        top_k=retrieval.top_k,
        similarity_threshold=retrieval.similarity_threshold,
        rerank_enabled=retrieval.rerank_enabled,
        hybrid_search_enabled=retrieval.hybrid_search_enabled,
        prompt_instruction=retrieval.prompt_instruction,
    )
    db.add(kb)
    _set_owners(db, kb, list(dict.fromkeys(payload.owner_ids + [user.id])))
    kb.acl_entries = [_new_acl(kb, entry) for entry in payload.acl]
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "KNOWLEDGE_BASE_DUPLICATE", "知识库 code 或名称已存在") from exc
    return kb


def update_knowledge_base(db: Session, kb: KnowledgeBase, payload: KnowledgeBaseUpdate) -> None:
    if payload.version != kb.row_version:
        raise AppError(409, "VERSION_CONFLICT", "知识库已被其他用户修改，请刷新后重试")
    supplied = payload.model_fields_set
    for field in ("name", "description", "department_id", "business_category", "selectable"):
        if field in supplied:
            setattr(kb, field, getattr(payload, field))
    if payload.owner_ids is not None:
        _set_owners(db, kb, payload.owner_ids)
    if payload.retrieval:
        for field, value in payload.retrieval.model_dump().items():
            setattr(kb, field, value)
    kb.row_version += 1
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "KNOWLEDGE_BASE_DUPLICATE", "同部门知识库名称已存在") from exc


def replace_acl(db: Session, kb: KnowledgeBase, entries: list[AclEntryInput], version: int) -> None:
    if version != kb.row_version:
        raise AppError(409, "VERSION_CONFLICT", "知识库权限已被修改，请刷新后重试")
    kb.acl_entries.clear()
    db.flush()
    kb.acl_entries.extend(_new_acl(kb, item) for item in entries)
    kb.row_version += 1
    db.flush()
