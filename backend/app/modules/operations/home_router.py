from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_current_user, get_page_params
from app.core.config import Settings, get_settings
from app.core.errors import AppError, paginated, success
from app.db.session import get_db
from app.modules.conversations.models import Conversation
from app.modules.documents.models import Document, DocumentVersion
from app.modules.documents.service import document_acl_allows, document_payload
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import knowledge_base_payload, list_accessible
from app.modules.operations.models import RecommendedQuestion
from app.modules.users.models import User


router = APIRouter()


@router.get("/home", summary="首页聚合")
def home(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    kbs = list_accessible(db, user, "search", selectable=True)
    today = date.today()
    recommendations = db.scalars(
        select(RecommendedQuestion)
        .where(
            RecommendedQuestion.enabled.is_(True),
            or_(RecommendedQuestion.valid_from.is_(None), RecommendedQuestion.valid_from <= today),
            or_(RecommendedQuestion.valid_to.is_(None), RecommendedQuestion.valid_to >= today),
        )
        .order_by(RecommendedQuestion.sort_order, RecommendedQuestion.id)
        .limit(12)
    ).all()
    documents = db.scalars(
        select(Document)
        .options(
            selectinload(Document.versions).selectinload(DocumentVersion.file),
            selectinload(Document.tags),
            selectinload(Document.knowledge_base).selectinload(KnowledgeBase.owners),
            selectinload(Document.knowledge_base).selectinload(KnowledgeBase.acl_entries),
        )
        .where(
            Document.knowledge_base_id.in_([kb.id for kb in kbs]) if kbs else Document.id.is_(None),
            Document.deleted_at.is_(None),
            Document.enabled.is_(True),
            Document.status == "ready",
        )
        .order_by(Document.updated_at.desc())
        .limit(20)
    ).unique().all()
    documents = [item for item in documents if document_acl_allows(db, item, user, "preview")][:6]
    categories = sorted({item.category for item in recommendations})
    return success(
        request,
        {
            "greeting": f"你好，{user.name}",
            "accessibleKnowledgeBases": [knowledge_base_payload(db, kb) for kb in kbs],
            "categories": categories,
            "hotQuestions": [
                {
                    "id": str(item.id),
                    "category": item.category,
                    "title": item.title,
                    "question": item.question,
                    "knowledgeBaseId": str(item.knowledge_base_id) if item.knowledge_base_id else None,
                }
                for item in recommendations
            ],
            "quickDocuments": [document_payload(db, item) for item in documents],
            "serviceStatus": {
                "status": "operational",
                "embedding": {
                    "provider": "qwen" if settings.embedding_provider == "qwen" else settings.embedding_provider,
                    "model": settings.embedding_model,
                    "configured": settings.embedding_provider == "local" or bool(settings.qwen_api_key),
                },
                "answering": {
                    "provider": "deepseek" if settings.llm_provider == "deepseek" else settings.llm_provider,
                    "model": settings.deepseek_model if settings.llm_provider == "deepseek" else "extractive",
                    "configured": settings.llm_provider == "extractive" or bool(settings.deepseek_api_key),
                },
            },
        },
    )


@router.get("/knowledge-bases/options", summary="可检索知识库选项")
def knowledge_base_options(
    request: Request,
    selectable: bool | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = list_accessible(db, user, "search", selectable=selectable)
    return success(request, [{"id": str(item.id), "code": item.code, "name": item.name, "category": item.business_category} for item in items])


@router.get("/search", summary="全局搜索")
def global_search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    type: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.user_type == "external":
        raise AppError(403, "EXTERNAL_SEARCH_FORBIDDEN", "外部人员账号仅开放首页问答")
    results = []
    kbs = list_accessible(db, user, "search")
    if type in {None, "knowledge_base"}:
        for kb in kbs:
            if q.lower() in f"{kb.name} {kb.code} {kb.description or ''}".lower():
                results.append({"type": "knowledge_base", "id": str(kb.id), "title": kb.name, "summary": kb.description, "target": f"/admin/knowledge-bases/{kb.id}"})
    if type in {None, "document"} and kbs:
        documents = db.scalars(
            select(Document)
            .options(
                selectinload(Document.versions).selectinload(DocumentVersion.file),
                selectinload(Document.tags),
                selectinload(Document.knowledge_base).selectinload(KnowledgeBase.owners),
                selectinload(Document.knowledge_base).selectinload(KnowledgeBase.acl_entries),
            )
            .where(Document.knowledge_base_id.in_([kb.id for kb in kbs]), Document.deleted_at.is_(None), or_(Document.display_name.ilike(f"%{q}%"), Document.document_no.ilike(f"%{q}%")))
            .order_by(Document.updated_at.desc())
            .limit(200)
        ).unique().all()
        for document in documents:
            if document_acl_allows(db, document, user, "preview"):
                results.append({"type": "document", "id": str(document.id), "title": document.display_name, "summary": document.notes, "target": f"/source?documentId={document.id}"})
    if type in {None, "conversation"}:
        conversations = db.scalars(
            select(Conversation).where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None), Conversation.title.ilike(f"%{q}%")).order_by(Conversation.updated_at.desc()).limit(100)
        ).all()
        results.extend({"type": "conversation", "id": str(item.id), "title": item.title, "summary": None, "target": f"/chat/{item.id}"} for item in conversations)
    total = len(results)
    return paginated(request, results[page.offset : page.offset + page.page_size], page=page.page, page_size=page.page_size, total=total)
