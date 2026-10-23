import hashlib
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.main import app
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, StoredFile
from app.modules.tasks.models import ProcessingTask, TaskItem, VectorIndexOutbox
from app.modules.tasks.service import create_task
from app.modules.users.models import User
from app.workers import document_pipeline
from conftest import login


def _headers(client):
    token = login(client).json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def test_editing_chunk_completes_reembedding_task(test_context, monkeypatch):
    client, SessionMaker, settings = test_context
    headers = _headers(client)
    knowledge_base = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={"name": "切片重向量测试库"},
    )
    assert knowledge_base.status_code == 201, knowledge_base.text
    knowledge_base_id = uuid.UUID(knowledge_base.json()["data"]["id"])

    with SessionMaker.begin() as db:
        user = db.scalar(select(User).where(User.account == "2026000001"))
        stored = StoredFile(
            storage_key="tests/chunk-reembedding.txt",
            original_name="切片重向量.txt",
            mime_type="text/plain",
            size_bytes=12,
            sha256="a" * 64,
            scan_status="clean",
            uploaded_by=user.id,
        )
        db.add(stored)
        db.flush()
        document = Document(
            knowledge_base_id=knowledge_base_id,
            display_name="切片重向量文档",
            status="ready",
            enabled=True,
            uploaded_by=user.id,
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_id=document.id,
            version_no=1,
            file_id=stored.id,
            is_current=True,
            process_status="ready",
            process_stage="published",
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        chunk = DocumentChunk(
            document_version_id=version.id,
            chunk_index=0,
            content="编辑前的切片内容",
            search_tokens="编辑前的切片内容",
            token_count=8,
            vector_id=str(uuid.uuid4()),
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
            vector_status="ready",
            content_hash="b" * 64,
        )
        db.add(chunk)
        db.flush()
        chunk_id = chunk.id

    monkeypatch.setattr(document_pipeline, "SessionLocal", SessionMaker)
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"environment": "development", "task_dispatch_mode": "inline"}
    )

    response = client.patch(
        f"/api/v1/admin/chunks/{chunk_id}",
        headers=headers,
        json={
            "content": "编辑后的切片内容，应立即完成重向量。",
            "reason": "回归测试",
            "version": 1,
        },
    )

    assert response.status_code == 200, response.text
    task_data = response.json()["data"]["task"]
    assert task_data["status"] == "succeeded", task_data
    assert task_data["progressPercent"] == 100
    with SessionMaker() as db:
        saved_chunk = db.get(DocumentChunk, chunk_id)
        saved_task = db.get(ProcessingTask, uuid.UUID(task_data["id"]))
        saved_item = db.scalar(select(TaskItem).where(TaskItem.task_id == saved_task.id))
        outbox = db.scalar(select(VectorIndexOutbox).where(VectorIndexOutbox.chunk_id == chunk_id))
        assert saved_chunk.vector_status == "ready"
        assert saved_chunk.content == "编辑后的切片内容，应立即完成重向量。"
        assert saved_task.status == "succeeded"
        assert saved_item.object_id == chunk_id
        assert saved_item.status == "succeeded"
        assert outbox.status == "processed"
        assert outbox.payload == {"contentHash": saved_chunk.content_hash}

    # Tasks created by the old endpoint had no TaskItem and were never sent to
    # Celery. The periodic recovery must still finish their pending chunks.
    with SessionMaker.begin() as db:
        saved_chunk = db.get(DocumentChunk, chunk_id)
        saved_chunk.content = "旧版接口留下的待处理切片"
        saved_chunk.search_tokens = saved_chunk.content
        saved_chunk.content_hash = hashlib.sha256(saved_chunk.content.encode()).hexdigest()
        saved_chunk.vector_status = "pending"
        saved_chunk.row_version += 1
        user = db.scalar(select(User).where(User.account == "2026000001"))
        legacy_task, _ = create_task(
            db,
            type="chunk_reembedding",
            created_by=user.id,
            knowledge_base_id=knowledge_base_id,
            document_id=saved_task.document_id,
            document_version_id=saved_chunk.document_version_id,
            progress_text="等待重新生成切片向量",
        )
        legacy_task_id = legacy_task.id

    assert document_pipeline.recover_queued_chunk_reembedding(settings) == 1
    with SessionMaker() as db:
        recovered_chunk = db.get(DocumentChunk, chunk_id)
        recovered_task = db.get(ProcessingTask, legacy_task_id)
        assert recovered_chunk.vector_status == "ready"
        assert recovered_task.status == "succeeded"
        assert recovered_task.progress_percent == 100
