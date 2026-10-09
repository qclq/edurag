from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.modules.documents.models import Document, DocumentVersion
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import create_task, get_task_or_404, update_task
from app.workers.document_pipeline import run_document_pipeline


def run_knowledge_base_rebuild(task_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    with SessionLocal() as db:
        parent = get_task_or_404(db, uuid.UUID(task_id))
        if not parent.knowledge_base_id:
            update_task(db, parent, status="failed", error_code="KB_REBUILD_SCOPE_MISSING", error_message="重建任务没有知识库范围")
            db.commit()
            return
        versions = db.scalars(
            select(DocumentVersion)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.knowledge_base_id == parent.knowledge_base_id,
                Document.deleted_at.is_(None),
                DocumentVersion.is_current.is_(True),
            )
            .order_by(Document.created_at)
        ).all()
        if not versions:
            update_task(db, parent, status="succeeded", stage="completed", progress_percent=100, progress_text="知识库没有需要重建的文档")
            db.commit()
            return
        update_task(db, parent, status="running", stage="rebuilding", progress_percent=0, progress_text=f"准备重建 {len(versions)} 个文档")
        db.commit()
        failed = 0
        for index, version in enumerate(versions, 1):
            db.refresh(parent)
            if parent.cancel_requested_at:
                update_task(db, parent, status="cancelled", progress_text="索引重建已取消")
                db.commit()
                return
            child, _ = create_task(
                db,
                type="document_reprocess",
                created_by=parent.created_by,
                knowledge_base_id=parent.knowledge_base_id,
                document_id=version.document_id,
                document_version_id=version.id,
                progress_text="知识库重建子任务",
            )
            db.commit()
            run_document_pipeline(str(child.id), settings)
            db.expire_all()
            child = db.get(ProcessingTask, child.id)
            if not child or child.status != "succeeded":
                failed += 1
            update_task(
                db,
                parent,
                status="running",
                stage="rebuilding",
                progress_percent=int(index * 100 / len(versions)),
                progress_text=f"已处理 {index}/{len(versions)} 个文档",
            )
            db.commit()
        update_task(
            db,
            parent,
            status="partial_success" if failed else "succeeded",
            stage="completed",
            progress_percent=100,
            progress_text=f"索引重建完成，失败 {failed} 个",
            error_code="KB_REBUILD_PARTIAL" if failed else None,
            error_message=f"{failed} 个文档重建失败" if failed else None,
        )
        db.commit()


def dispatch_knowledge_base_rebuild(task_id: str, settings: Settings) -> None:
    if settings.task_dispatch_mode == "inline":
        run_knowledge_base_rebuild(task_id, settings)
        return
    rebuild_knowledge_base.delay(task_id)


try:
    from app.workers.celery_app import celery_app

    @celery_app.task(name="edurag.rebuild_knowledge_base", autoretry_for=(), max_retries=0)
    def rebuild_knowledge_base(task_id: str) -> None:
        run_knowledge_base_rebuild(task_id)
except ImportError:
    class _UnavailableTask:
        def delay(self, task_id: str) -> None:
            raise RuntimeError("Celery 未安装，无法派发后台任务")

    rebuild_knowledge_base = _UnavailableTask()
