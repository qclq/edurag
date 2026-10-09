from __future__ import annotations

import hashlib
import io
import re
import time
import uuid
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.security import utcnow
from app.db.session import SessionLocal
from app.integrations.embedding import EmbeddingProviderError, embed_texts
from app.integrations.storage import get_storage
from app.modules.documents.models import Document, DocumentAclEntry, DocumentChunk, DocumentVersion
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.tasks.models import ProcessingTask, VectorIndexOutbox
from app.modules.tasks.service import get_task_or_404, update_task


def _extract_pdf(content: bytes) -> list[tuple[int | None, str]]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF 解析依赖 PyMuPDF 未安装") from exc
    try:
        pdf = fitz.open(stream=content, filetype="pdf")
        if pdf.needs_pass:
            raise RuntimeError("不支持加密 PDF")
        return [(index + 1, page.get_text("text")) for index, page in enumerate(pdf)]
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("PDF 文件损坏或无法解析") from exc


def _extract_docx(content: bytes) -> list[tuple[int | None, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(f"{namespace}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if text.strip():
                paragraphs.append(text.strip())
        return [(None, "\n".join(paragraphs))]
    except Exception as exc:
        raise RuntimeError("DOCX 文件损坏或无法解析") from exc


def extract_pages(file_name: str, content: bytes) -> list[tuple[int | None, str]]:
    extension = file_name.rsplit(".", 1)[-1].lower()
    if extension == "pdf":
        return _extract_pdf(content)
    if extension == "docx":
        return _extract_docx(content)
    if extension in {"txt", "md"}:
        return [(None, content.decode("utf-8"))]
    raise RuntimeError("不支持该文件格式")


def split_chunks(pages: list[tuple[int | None, str]], chunk_size: int, overlap: int):
    chunks: list[dict[str, object]] = []
    for page_number, text in pages:
        normalized = re.sub(r"[ \t]+", " ", text).strip()
        if not normalized:
            continue
        cursor = 0
        while cursor < len(normalized):
            upper = min(len(normalized), cursor + chunk_size)
            if upper < len(normalized):
                boundary = max(normalized.rfind("\n", cursor, upper), normalized.rfind("。", cursor, upper))
                if boundary > cursor + chunk_size // 2:
                    upper = boundary + 1
            part = normalized[cursor:upper].strip()
            if part:
                heading = part.splitlines()[0][:200] if "\n" in part else None
                chunks.append(
                    {
                        "content": part,
                        "page_start": page_number,
                        "page_end": page_number,
                        "char_start": cursor,
                        "char_end": upper,
                        "section": heading,
                    }
                )
            if upper >= len(normalized):
                break
            cursor = max(cursor + 1, upper - overlap)
    return chunks


def _entries_to_tokens(entries) -> set[str]:
    values: set[str] = set()
    for entry in entries:
        if not entry.can_search:
            continue
        if entry.is_public:
            values.add("public")
        elif entry.role_id:
            values.add(f"role:{entry.role_id}")
        elif entry.department_id:
            values.add(f"department:{entry.department_id}")
        elif entry.user_id:
            values.add(f"user:{entry.user_id}")
    return values


def _search_acl_tokens(db: Session, kb: KnowledgeBase, document: Document) -> list[str]:
    values: set[str] = {f"user:{kb.created_by}"}
    values.update(f"user:{owner.id}" for owner in kb.owners)
    values.update(_entries_to_tokens(kb.acl_entries))
    document_entries = db.scalars(
        select(DocumentAclEntry).where(DocumentAclEntry.document_id == document.id)
    ).all()
    if document_entries:
        # The KB id filter proves KB access; document tokens apply the narrowing layer.
        values = _entries_to_tokens(document_entries)
    return sorted(values)


def _load_objects(db: Session, task_id) -> tuple[ProcessingTask, DocumentVersion, Document, KnowledgeBase]:
    task = get_task_or_404(db, task_id)
    version = db.scalar(
        select(DocumentVersion)
        .options(selectinload(DocumentVersion.file), selectinload(DocumentVersion.document))
        .where(DocumentVersion.id == task.document_version_id)
    )
    if not version:
        raise RuntimeError("文档版本不存在")
    document = db.scalar(
        select(Document).where(Document.id == version.document_id, Document.deleted_at.is_(None))
    )
    if not document:
        raise RuntimeError("文档不存在或已删除")
    kb = db.scalar(
        select(KnowledgeBase)
        .options(selectinload(KnowledgeBase.owners), selectinload(KnowledgeBase.acl_entries))
        .where(KnowledgeBase.id == document.knowledge_base_id)
    )
    if not kb:
        raise RuntimeError("知识库不存在")
    return task, version, document, kb


def run_document_pipeline(task_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    with SessionLocal() as db:
        task = None
        version = None
        document = None
        try:
            task, version, document, kb = _load_objects(db, uuid.UUID(task_id))
            if task.cancel_requested_at:
                update_task(db, task, status="cancelled", progress_text="任务已取消")
                db.commit()
                return
            update_task(db, task, status="running", stage="parsing", progress_percent=10, progress_text="正在解析文档")
            version.process_status = "processing"
            version.process_stage = "parsing"
            db.commit()

            with get_storage(settings).open(version.file.storage_key) as stream:
                content = stream.read()
            pages = extract_pages(version.file.original_name, content)
            chunks_data = split_chunks(pages, version.chunk_size, version.chunk_overlap)
            if not chunks_data:
                raise RuntimeError("文档中没有可用文本；扫描件请启用 OCR provider")

            update_task(db, task, status="running", stage="chunking", progress_percent=35, progress_text="正在切分文本")
            version.process_stage = "chunking"
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id))
            chunks: list[DocumentChunk] = []
            for index, item in enumerate(chunks_data):
                text = str(item["content"])
                chunk = DocumentChunk(
                    document_version_id=version.id,
                    chunk_index=index,
                    title=document.display_name,
                    section=item["section"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    char_start=item["char_start"],
                    char_end=item["char_end"],
                    content=text,
                    token_count=max(1, len(text) // 2),
                    search_tokens=text,
                    embedding_model=settings.embedding_model,
                    embedding_dimension=settings.embedding_dimension,
                    vector_status="pending",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                )
                db.add(chunk)
                chunks.append(chunk)
            db.flush()

            update_task(db, task, status="running", stage="embedding", progress_percent=55, progress_text="正在调用 Qwen 生成向量")
            version.process_stage = "embedding"
            db.commit()
            vectors = embed_texts([chunk.content for chunk in chunks], settings)

            collection = f"{settings.milvus_collection_prefix}_{re.sub(r'[^a-zA-Z0-9_]', '_', settings.embedding_model)}"
            tokens = _search_acl_tokens(db, kb, document)
            expires_at = int(datetime.combine(document.expires_at, datetime.min.time(), tzinfo=UTC).timestamp()) if document.expires_at else 0
            vector_rows = []
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk.vector_id = str(chunk.id)
                outbox = VectorIndexOutbox(
                    operation="upsert",
                    chunk_id=chunk.id,
                    document_version_id=version.id,
                    collection_name=collection,
                    payload={"contentHash": chunk.content_hash},
                )
                db.add(outbox)
                vector_rows.append(
                    {
                        "chunk_id": str(chunk.id),
                        "embedding": vector,
                        "knowledge_base_id": str(kb.id),
                        "document_id": str(document.id),
                        "document_version_id": str(version.id),
                        "is_active": True,
                        "expires_at": expires_at,
                        "search_acl_tokens": tokens,
                        "embedding_model": settings.embedding_model,
                    }
                )
            db.commit()

            update_task(db, task, status="running", stage="indexing", progress_percent=80, progress_text="正在发布 Milvus 索引")
            version.process_stage = "indexing"
            db.commit()
            if settings.milvus_enabled:
                from app.integrations.milvus import MilvusIndex

                MilvusIndex(settings).upsert(collection, vector_rows)
            for chunk in chunks:
                chunk.vector_status = "ready"
            for event in db.scalars(
                select(VectorIndexOutbox).where(
                    VectorIndexOutbox.document_version_id == version.id,
                    VectorIndexOutbox.status == "pending",
                )
            ):
                event.status = "processed"
            for old in document.versions:
                old.is_current = False
            version.is_current = True
            version.process_status = "ready"
            version.process_stage = "published"
            version.processed_at = utcnow()
            document.current_version_id = version.id
            document.status = "ready"
            document.enabled = True
            update_task(db, task, status="succeeded", stage="published", progress_percent=100, progress_text="文档已入库")
            db.commit()
        except Exception as exc:
            db.rollback()
            if task is not None:
                task = db.get(ProcessingTask, task.id)
                version = db.get(DocumentVersion, version.id) if version else None
                document = db.get(Document, document.id) if document else None
                code = getattr(exc, "code", "DOCUMENT_PROCESSING_FAILED")
                message = str(exc)[:1000] or "文档处理失败"
                if version:
                    version.process_status = "failed"
                    version.failure_code = code
                    version.failure_message = message
                if document and document.current_version_id is None:
                    document.status = "failed"
                update_task(db, task, status="failed", stage=task.stage, progress_text="文档处理失败", error_code=code, error_message=message)
                db.commit()


def dispatch_document_pipeline(task_id: str, settings: Settings) -> None:
    if settings.task_dispatch_mode == "inline":
        run_document_pipeline(task_id, settings)
        return
    process_document.delay(task_id)


try:
    from app.workers.celery_app import celery_app

    @celery_app.task(name="edurag.process_document", autoretry_for=(), max_retries=0)
    def process_document(task_id: str) -> None:
        run_document_pipeline(task_id)
except ImportError:
    class _UnavailableTask:
        def delay(self, task_id: str) -> None:
            raise RuntimeError("Celery 未安装，无法派发后台任务")

    process_document = _UnavailableTask()
