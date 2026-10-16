import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_current_user, get_page_params, require_permissions
from app.core.config import Settings, get_settings
from app.core.errors import AppError, paginated, success
from app.core.security import utcnow
from app.db.session import get_db
from app.integrations.llm import LlmProviderError, generate_answer
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.schemas import (
    AclReplace,
    KnowledgeBaseCreate,
    KnowledgeBaseDelete,
    KnowledgeBaseStatus,
    KnowledgeBaseUpdate,
    RebuildRequest,
    RetrievalTest,
)
from app.modules.knowledge_bases.service import (
    can_access,
    create_knowledge_base,
    ensure_access,
    get_kb_or_404,
    knowledge_base_payload,
    replace_acl,
    update_knowledge_base,
)
from app.modules.operations.service import record_audit
from app.modules.operations.retention import purge_knowledge_base_operations
from app.modules.rag.service import retrieve
from app.modules.tasks.service import create_task, task_payload
from app.modules.users.models import User
from app.workers.maintenance import dispatch_knowledge_base_rebuild


router = APIRouter()


@router.get("/knowledge-bases", summary="知识库分页")
def list_knowledge_bases(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    department_id: uuid.UUID | None = Query(default=None, alias="departmentId"),
    page: PageParams = Depends(get_page_params),
    user: User = Depends(require_permissions("kb.read")),
    db: Session = Depends(get_db),
):
    statement = (
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
        .where(KnowledgeBase.deleted_at.is_(None))
        .order_by(KnowledgeBase.updated_at.desc())
    )
    if q:
        pattern = f"%{q}%"
        statement = statement.where(or_(KnowledgeBase.name.ilike(pattern), KnowledgeBase.code.ilike(pattern)))
    if status:
        statement = statement.where(KnowledgeBase.status == status)
    if department_id:
        statement = statement.where(KnowledgeBase.department_id == department_id)
    visible = [item for item in db.scalars(statement).unique().all() if can_access(item, user, "manage")]
    items = visible[page.offset : page.offset + page.page_size]
    return paginated(
        request,
        [knowledge_base_payload(db, item) for item in items],
        page=page.page,
        page_size=page.page_size,
        total=len(visible),
    )


@router.post("/knowledge-bases", status_code=201, summary="新建知识库")
def post_knowledge_base(
    payload: KnowledgeBaseCreate,
    request: Request,
    user: User = Depends(require_permissions("kb.create")),
    db: Session = Depends(get_db),
):
    kb = create_knowledge_base(db, user, payload)
    record_audit(db, actor=user, module="knowledge_base", action="create", target_type="knowledge_base", target_id=kb.id, request=request, after=payload.model_dump())
    db.commit()
    kb = get_kb_or_404(db, kb.id)
    return success(request, knowledge_base_payload(db, kb, detailed=True), status_code=201)


@router.get("/knowledge-bases/{kb_id}", summary="知识库详情")
def get_knowledge_base(
    kb_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("kb.read")),
    db: Session = Depends(get_db),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    return success(request, knowledge_base_payload(db, kb, detailed=True))


@router.patch("/knowledge-bases/{kb_id}", summary="更新知识库")
def patch_knowledge_base(
    kb_id: uuid.UUID,
    payload: KnowledgeBaseUpdate,
    request: Request,
    user: User = Depends(require_permissions("kb.update")),
    db: Session = Depends(get_db),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    before = knowledge_base_payload(db, kb, detailed=True)
    update_knowledge_base(db, kb, payload)
    record_audit(db, actor=user, module="knowledge_base", action="update", target_type="knowledge_base", target_id=kb.id, request=request, before=before, after=payload.model_dump())
    db.commit()
    return success(request, knowledge_base_payload(db, get_kb_or_404(db, kb.id), detailed=True))


@router.post("/knowledge-bases/{kb_id}/status", summary="启停知识库")
def knowledge_base_status(
    kb_id: uuid.UUID,
    payload: KnowledgeBaseStatus,
    request: Request,
    user: User = Depends(require_permissions("kb.status")),
    db: Session = Depends(get_db),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    previous = kb.status
    kb.status = payload.status
    kb.row_version += 1
    record_audit(db, actor=user, module="knowledge_base", action="status", target_type="knowledge_base", target_id=kb.id, request=request, before={"status": previous}, after=payload.model_dump())
    db.commit()
    return success(request, {"id": str(kb.id), "status": kb.status, "affectedSearchScope": previous != kb.status})


@router.delete("/knowledge-bases/{kb_id}", status_code=204, summary="软删除知识库")
def delete_knowledge_base(
    kb_id: uuid.UUID,
    payload: KnowledgeBaseDelete,
    request: Request,
    user: User = Depends(require_permissions("kb.delete")),
    db: Session = Depends(get_db),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    if payload.confirm_name != kb.name:
        raise AppError(400, "CONFIRMATION_MISMATCH", "确认名称与知识库名称不一致")
    cleanup = purge_knowledge_base_operations(db, kb.id)
    kb.deleted_at = utcnow()
    kb.status = "disabled"
    record_audit(
        db,
        actor=user,
        module="knowledge_base",
        action="delete",
        target_type="knowledge_base",
        target_id=kb.id,
        request=request,
        after={"operationalRecordsRemoved": cleanup},
    )
    db.commit()
    return Response(status_code=204)


@router.get("/knowledge-bases/{kb_id}/acl", summary="知识库 ACL")
def get_acl(
    kb_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_permissions("kb.permission.read")),
    db: Session = Depends(get_db),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    data = knowledge_base_payload(db, kb, detailed=True)
    return success(request, {"entries": data["acl"], "version": kb.row_version})


@router.put("/knowledge-bases/{kb_id}/acl", summary="替换知识库 ACL")
def put_acl(
    kb_id: uuid.UUID,
    payload: AclReplace,
    request: Request,
    user: User = Depends(require_permissions("kb.permission.update")),
    db: Session = Depends(get_db),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    before = knowledge_base_payload(db, kb, detailed=True)["acl"]
    replace_acl(db, kb, payload.entries, payload.version)
    record_audit(db, actor=user, module="knowledge_base", action="acl.update", target_type="knowledge_base", target_id=kb.id, request=request, before=before, after=[item.model_dump() for item in payload.entries])
    db.commit()
    data = knowledge_base_payload(db, get_kb_or_404(db, kb.id), detailed=True)
    return success(request, {"entries": data["acl"], "version": data["version"]})


@router.post("/knowledge-bases/{kb_id}/rebuild-index", status_code=202, summary="重建知识库索引")
def rebuild_index(
    kb_id: uuid.UUID,
    payload: RebuildRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_permissions("kb.rebuild")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    task, _ = create_task(db, type="knowledge_base_rebuild", created_by=user.id, knowledge_base_id=kb.id, idempotency_key=idempotency_key, progress_text="等待重建索引")
    record_audit(db, actor=user, module="knowledge_base", action="rebuild", target_type="knowledge_base", target_id=kb.id, request=request, after={"reason": payload.reason, "taskId": str(task.id)})
    db.commit()
    if settings.environment != "test":
        try:
            dispatch_knowledge_base_rebuild(str(task.id), settings)
        except RuntimeError:
            pass
    return success(request, task_payload(task, include_events=False), status_code=202)


@router.post("/knowledge-bases/{kb_id}/retrieval-tests", summary="知识库检索测试")
def retrieval_test(
    kb_id: uuid.UUID,
    payload: RetrievalTest,
    request: Request,
    user: User = Depends(require_permissions("kb.test")),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    kb = get_kb_or_404(db, kb_id)
    ensure_access(kb, user, "manage")
    chunks, summary = retrieve(db, user, payload.query, settings, knowledge_base_ids=[kb.id], top_k=payload.top_k or kb.top_k, min_score=payload.threshold if payload.threshold is not None else kb.similarity_threshold)
    try:
        answer, model = generate_answer(payload.query, chunks, settings)
    except LlmProviderError as exc:
        answer, model = None, None
        summary["answerError"] = {"code": exc.code, "message": str(exc)}
    record_audit(db, actor=user, module="knowledge_base", action="retrieval.test", target_type="knowledge_base", target_id=kb.id, request=request, after={"query": payload.query, "resultCount": len(chunks)})
    db.commit()
    return success(request, {"chunks": chunks, "testAnswer": answer, "model": model, "timing": summary})
