from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from urllib.parse import quote

from app.api.deps import PageParams, get_page_params, require_permissions
from app.core.config import Settings, get_settings
from app.core.errors import AppError, paginated, success
from app.core.security import utcnow
from app.db.session import get_db
from app.integrations.llm import LlmProviderError, generate_answer
from app.integrations.storage import get_storage
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, PendingUpload
from app.modules.documents.schemas import (
    ChunkUpdate,
    DocumentDelete,
    DocumentRetrievalTest,
    DocumentStatus,
    DocumentUpdate,
    IngestionCreate,
    NewVersion,
    ReprocessRequest,
)
from app.modules.documents.service import (
    chunk_payload,
    create_ingestions,
    create_upload,
    document_payload,
    get_document_or_404,
    get_upload,
    update_document,
    upload_payload,
)
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import can_access, get_kb_or_404
from app.modules.operations.service import record_audit
from app.modules.rag.service import retrieve
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import create_task, task_payload
from app.modules.users.models import User
from app.workers.document_pipeline import dispatch_document_pipeline


router = APIRouter()


@router.get("/document-upload-config", summary="文档上传规则")
def upload_config(
    request: Request,
    knowledge_base_id: uuid.UUID | None = Query(default=None, alias="knowledgeBaseId"),
    user: User = Depends(require_permissions("document.create")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    defaults = {"ocrEnabled": False, "chunkSize": 800, "chunkOverlap": 100}
    if knowledge_base_id:
        kb = get_kb_or_404(db, knowledge_base_id)
        if not can_access(kb, user, "manage"):
            raise AppError(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在")
        defaults.update({"chunkSize": kb.chunk_size, "chunkOverlap": kb.chunk_overlap})
    return success(
        request,
        {
            "allowedExtensions": settings.upload_extensions,
            "maxFileSizeBytes": settings.upload_max_file_mb * 1024 * 1024,
            "maxFiles": settings.upload_max_files,
            "categories": ["规章制度", "办事指南", "通知公告", "教学资料", "其他"],
            "defaults": defaults,
        },
    )


@router.post("/uploads", status_code=201, summary="暂存单个文件")
async def post_upload(
    request: Request,
    file: UploadFile = File(...),
    client_file_id: str = Form(..., alias="clientFileId", min_length=1, max_length=200),
    user: User = Depends(require_permissions("document.create")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    content = await file.read(settings.upload_max_file_mb * 1024 * 1024 + 1)
    upload = create_upload(
        db,
        user,
        client_file_id=client_file_id,
        file_name=file.filename or "",
        content=content,
        content_type=file.content_type,
        settings=settings,
    )
    record_audit(db, actor=user, module="document", action="upload", target_type="upload", target_id=upload.id, request=request, after={"fileName": upload.file.original_name, "size": upload.file.size_bytes})
    db.commit()
    return success(request, upload_payload(upload), status_code=201)


@router.delete("/uploads/{upload_id}", status_code=204, summary="取消暂存文件")
def delete_upload(
    upload_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("document.create")),
    db: Session = Depends(get_db),
):
    upload = get_upload(db, upload_id, user)
    upload.status = "cancelled"
    record_audit(db, actor=user, module="document", action="upload.cancel", target_type="upload", target_id=upload.id, request=request)
    db.commit()
    return Response(status_code=204)


@router.post("/document-ingestions", status_code=202, summary="批量提交文档入库")
def post_ingestions(
    payload: IngestionCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_permissions("document.create")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    created = create_ingestions(db, user, payload)
    task_ids = []
    for document, version, task in created:
        if task and idempotency_key and len(created) == 1:
            task.idempotency_key = idempotency_key
        if task:
            task_ids.append(str(task.id))
        record_audit(db, actor=user, module="document", action="ingest", target_type="document", target_id=document.id, request=request, after={"versionId": str(version.id), "draft": payload.save_as_draft})
    db.commit()
    if settings.environment != "test":
        for task_id in task_ids:
            try:
                dispatch_document_pipeline(task_id, settings)
            except RuntimeError:
                pass
    items = [
        {
            "document": document_payload(db, get_document_or_404(db, document.id, user), detailed=True),
            "task": task_payload(task, include_events=False) if task else None,
        }
        for document, _, task in created
    ]
    return success(request, {"items": items}, status_code=201 if payload.save_as_draft else 202)


@router.get("/documents", summary="文档分页")
def list_documents(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    knowledge_base_id: uuid.UUID | None = Query(default=None, alias="knowledgeBaseId"),
    status: str | None = None,
    category: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("document.read")),
    db: Session = Depends(get_db),
):
    statement = (
        select(Document)
        .options(
            selectinload(Document.versions).selectinload(DocumentVersion.file),
            selectinload(Document.tags),
            selectinload(Document.knowledge_base).selectinload(KnowledgeBase.owners),
            selectinload(Document.knowledge_base).selectinload(KnowledgeBase.acl_entries),
        )
        .where(Document.deleted_at.is_(None))
        .order_by(Document.updated_at.desc())
    )
    if q:
        statement = statement.where(or_(Document.display_name.ilike(f"%{q}%"), Document.document_no.ilike(f"%{q}%")))
    if knowledge_base_id:
        statement = statement.where(Document.knowledge_base_id == knowledge_base_id)
    if status:
        statement = statement.where(Document.status == status)
    if category:
        statement = statement.where(Document.category == category)
    visible = [item for item in db.scalars(statement).unique().all() if can_access(item.knowledge_base, user, "manage")]
    items = visible[page.offset : page.offset + page.page_size]
    return paginated(request, [document_payload(db, item) for item in items], page=page.page, page_size=page.page_size, total=len(visible))


@router.get("/documents/{document_id}", summary="管理端文档详情")
def get_document(
    document_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("document.read")),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user)
    return success(request, document_payload(db, document, detailed=True))


@router.patch("/documents/{document_id}", summary="更新文档元数据")
def patch_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    request: Request,
    user: User = Depends(require_permissions("document.update")),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user)
    before = document_payload(db, document, detailed=True)
    update_document(db, document, payload)
    record_audit(db, actor=user, module="document", action="update", target_type="document", target_id=document.id, request=request, before=before, after=payload.model_dump())
    db.commit()
    return success(request, document_payload(db, get_document_or_404(db, document.id, user), detailed=True))


@router.post("/documents/{document_id}/versions", status_code=202, summary="上传新版本")
def new_version(
    document_id: uuid.UUID,
    payload: NewVersion,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_permissions("document.update")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    document = get_document_or_404(db, document_id, user)
    upload = get_upload(db, payload.upload_id, user)
    version_no = max((item.version_no for item in document.versions), default=0) + 1
    version = DocumentVersion(
        document_id=document.id,
        version_no=version_no,
        file_id=upload.file_id,
        process_status="pending",
        chunk_size=document.knowledge_base.chunk_size,
        chunk_overlap=document.knowledge_base.chunk_overlap,
    )
    db.add(version)
    db.flush()
    upload.status = "consumed"
    upload.consumed_at = utcnow()
    task, _ = create_task(db, type="document_version", created_by=user.id, knowledge_base_id=document.knowledge_base_id, document_id=document.id, document_version_id=version.id, idempotency_key=idempotency_key, progress_text="等待处理新版本")
    record_audit(db, actor=user, module="document", action="version.create", target_type="document", target_id=document.id, request=request, after={"versionNo": version_no, "switchPolicy": payload.switch_policy})
    db.commit()
    if settings.environment != "test":
        try:
            dispatch_document_pipeline(str(task.id), settings)
        except RuntimeError:
            pass
    return success(request, {"versionId": str(version.id), "versionNo": version.version_no, "task": task_payload(task, include_events=False)}, status_code=202)


@router.post("/documents/{document_id}/reprocess", status_code=202, summary="重新处理文档")
def reprocess_document(
    document_id: uuid.UUID,
    payload: ReprocessRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_permissions("document.reprocess")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    document = get_document_or_404(db, document_id, user)
    current = next((item for item in document.versions if item.id == document.current_version_id), None) or next((item for item in document.versions if item.is_current), None)
    if not current:
        raise AppError(409, "DOCUMENT_VERSION_MISSING", "文档没有可重新处理的版本")
    current.ocr_enabled = payload.ocr_enabled
    current.chunk_size = payload.chunk_size
    current.chunk_overlap = payload.chunk_overlap
    current.process_status = "pending"
    task, _ = create_task(db, type="document_reprocess", created_by=user.id, knowledge_base_id=document.knowledge_base_id, document_id=document.id, document_version_id=current.id, idempotency_key=idempotency_key, progress_text="等待重新处理")
    record_audit(db, actor=user, module="document", action="reprocess", target_type="document", target_id=document.id, request=request, after=payload.model_dump())
    db.commit()
    if settings.environment != "test":
        try:
            dispatch_document_pipeline(str(task.id), settings)
        except RuntimeError:
            pass
    return success(request, task_payload(task, include_events=False), status_code=202)


@router.post("/documents/{document_id}/status", summary="启停文档")
def document_status(
    document_id: uuid.UUID,
    payload: DocumentStatus,
    request: Request,
    user: User = Depends(require_permissions("document.status")),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user)
    document.enabled = payload.status == "enabled"
    if not document.enabled:
        document.status = "disabled"
    elif document.current_version_id:
        document.status = "ready"
    document.row_version += 1
    record_audit(db, actor=user, module="document", action="status", target_type="document", target_id=document.id, request=request, after=payload.model_dump())
    db.commit()
    return success(request, {"id": str(document.id), "status": document.status, "enabled": document.enabled})


@router.delete("/documents/{document_id}", status_code=204, summary="软删除文档")
def delete_document(
    document_id: uuid.UUID,
    payload: DocumentDelete,
    request: Request,
    user: User = Depends(require_permissions("document.delete")),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user)
    if payload.confirm_name != document.display_name:
        raise AppError(400, "CONFIRMATION_MISMATCH", "确认名称与文档名称不一致")
    document.deleted_at = utcnow()
    document.enabled = False
    record_audit(db, actor=user, module="document", action="delete", target_type="document", target_id=document.id, request=request)
    db.commit()
    return Response(status_code=204)


@router.get("/documents/{document_id}/timeline", summary="文档处理时间线")
def document_timeline(
    document_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("document.read")),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user)
    tasks = db.scalars(
        select(ProcessingTask).options(selectinload(ProcessingTask.events)).where(ProcessingTask.document_id == document.id).order_by(ProcessingTask.created_at)
    ).all()
    events = [
        {"taskId": str(task.id), "taskNo": task.task_no, "sequence": event.sequence, "type": event.event_type, "stage": event.stage, "data": event.payload, "occurredAt": event.occurred_at}
        for task in tasks for event in task.events
    ]
    return success(request, events)


@router.get("/documents/{document_id}/chunks", summary="文档切片分页")
def document_chunks(
    document_id: uuid.UUID,
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    page_number: int | None = Query(default=None, alias="pageNumber", ge=1),
    section: str | None = None,
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("document.read")),
    db: Session = Depends(get_db),
):
    document = get_document_or_404(db, document_id, user)
    version_id = document.current_version_id or next((item.id for item in document.versions if item.is_current), None)
    statement = select(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
    if q:
        statement = statement.where(DocumentChunk.content.ilike(f"%{q}%"))
    if page_number:
        statement = statement.where(DocumentChunk.page_start <= page_number, DocumentChunk.page_end >= page_number)
    if section:
        statement = statement.where(DocumentChunk.section.ilike(f"%{section}%"))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.order_by(DocumentChunk.chunk_index).offset(page.offset).limit(page.page_size)).all()
    return paginated(request, [chunk_payload(item, document) for item in items], page=page.page, page_size=page.page_size, total=total)


@router.patch("/chunks/{chunk_id}", summary="编辑切片并创建重向量任务")
def patch_chunk(
    chunk_id: uuid.UUID,
    payload: ChunkUpdate,
    request: Request,
    user: User = Depends(require_permissions("chunk.update")),
    db: Session = Depends(get_db),
):
    chunk = db.get(DocumentChunk, chunk_id)
    if not chunk:
        raise AppError(404, "CHUNK_NOT_FOUND", "切片不存在")
    version = db.get(DocumentVersion, chunk.document_version_id)
    document = get_document_or_404(db, version.document_id, user)
    if chunk.row_version != payload.version:
        raise AppError(409, "VERSION_CONFLICT", "切片已被其他用户修改，请刷新后重试")
    before = chunk.content
    chunk.content = payload.content
    chunk.search_tokens = payload.content
    chunk.content_hash = hashlib.sha256(payload.content.encode()).hexdigest()
    chunk.vector_status = "pending"
    chunk.row_version += 1
    task, _ = create_task(db, type="chunk_reembedding", created_by=user.id, knowledge_base_id=document.knowledge_base_id, document_id=document.id, document_version_id=version.id, progress_text="等待重新生成切片向量")
    record_audit(db, actor=user, module="document", action="chunk.update", target_type="chunk", target_id=chunk.id, request=request, before={"content": before}, after={"content": payload.content, "reason": payload.reason})
    db.commit()
    return success(request, {"chunk": chunk_payload(chunk, document), "task": task_payload(task, include_events=False)})


@router.post("/documents/{document_id}/retrieval-tests", summary="文档内检索测试")
def document_retrieval_test(
    document_id: uuid.UUID,
    payload: DocumentRetrievalTest,
    request: Request,
    user: User = Depends(require_permissions("kb.test")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    document = get_document_or_404(db, document_id, user)
    chunks, timing = retrieve(db, user, payload.query, settings, knowledge_base_ids=[document.knowledge_base_id], document_id=document.id, top_k=payload.top_k, min_score=payload.min_score)
    try:
        answer, model = generate_answer(payload.query, chunks, settings)
    except LlmProviderError as exc:
        answer, model = None, None
        timing["answerError"] = {"code": exc.code, "message": str(exc)}
    record_audit(db, actor=user, module="document", action="retrieval.test", target_type="document", target_id=document.id, request=request, after={"query": payload.query, "resultCount": len(chunks)})
    db.commit()
    return success(request, {"chunks": chunks, "testAnswer": answer, "model": model, "timing": timing})
