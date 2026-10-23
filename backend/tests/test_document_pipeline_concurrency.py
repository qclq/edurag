import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.main import app
from app.modules.documents import router as documents_router
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import create_task
from app.workers import document_pipeline
from conftest import login


def _headers(client):
    token = login(client).json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def test_same_document_version_is_not_processed_concurrently(test_context, monkeypatch):
    client, SessionMaker, settings = test_context
    headers = _headers(client)
    knowledge_base = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={"name": "并发处理测试库"},
    )
    assert knowledge_base.status_code == 201, knowledge_base.text

    upload = client.post(
        "/api/v1/admin/uploads",
        headers=headers,
        data={"clientFileId": "pipeline-concurrency-source"},
        files={"file": ("rules.txt", "同一版本只能由一个任务处理。".encode(), "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    ingestion = client.post(
        "/api/v1/admin/document-ingestions",
        headers=headers,
        json={
            "items": [
                {
                    "uploadId": upload.json()["data"]["uploadId"],
                    "metadata": {
                        "knowledgeBaseId": knowledge_base.json()["data"]["id"],
                        "displayName": "并发处理规则",
                        "category": "规章制度",
                    },
                }
            ],
            "processingConfig": {"ocrEnabled": False, "chunkSize": 500, "chunkOverlap": 50},
            "saveAsDraft": False,
        },
    )
    assert ingestion.status_code == 202, ingestion.text
    first_task_id = uuid.UUID(ingestion.json()["data"]["items"][0]["task"]["id"])

    with SessionMaker.begin() as db:
        first = db.get(ProcessingTask, first_task_id)
        second, _ = create_task(
            db,
            type="document_ingestion",
            created_by=first.created_by,
            knowledge_base_id=first.knowledge_base_id,
            document_id=first.document_id,
            document_version_id=first.document_version_id,
            progress_text="并发重试任务",
        )
        second_task_id = second.id

    monkeypatch.setattr(document_pipeline, "SessionLocal", SessionMaker)
    original_embed = document_pipeline.embed_texts
    nested_started = False

    def overlap_with_retry(texts, current_settings):
        nonlocal nested_started
        if not nested_started:
            nested_started = True
            document_pipeline.run_document_pipeline(str(second_task_id), current_settings)
        return original_embed(texts, current_settings)

    monkeypatch.setattr(document_pipeline, "embed_texts", overlap_with_retry)
    document_pipeline.run_document_pipeline(str(first_task_id), settings)
    # A contending task stays queued, then succeeds once the first task releases
    # the per-version lock. Without the lock it deletes the first task's chunks.
    document_pipeline.run_document_pipeline(str(second_task_id), settings)

    with SessionMaker() as db:
        tasks = db.scalars(
            select(ProcessingTask).where(ProcessingTask.id.in_([first_task_id, second_task_id]))
        ).all()
        outcomes = [(task.status, task.error_message) for task in tasks]
        assert {task.status for task in tasks} == {"succeeded"}, outcomes
        assert all("expected to update" not in (task.error_message or "") for task in tasks)


def test_broker_dispatch_failure_is_visible_in_task_center(test_context, monkeypatch):
    client, SessionMaker, settings = test_context
    headers = _headers(client)
    knowledge_base = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={"name": "派发失败测试库"},
    )
    upload = client.post(
        "/api/v1/admin/uploads",
        headers=headers,
        data={"clientFileId": "pipeline-dispatch-failure"},
        files={"file": ("notice.txt", "任务队列故障必须可见。".encode(), "text/plain")},
    )
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"environment": "development", "task_dispatch_mode": "celery"}
    )

    def unavailable_broker(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(documents_router, "dispatch_document_pipeline", unavailable_broker)
    ingestion = client.post(
        "/api/v1/admin/document-ingestions",
        headers=headers,
        json={
            "items": [
                {
                    "uploadId": upload.json()["data"]["uploadId"],
                    "metadata": {
                        "knowledgeBaseId": knowledge_base.json()["data"]["id"],
                        "displayName": "派发失败公告",
                        "category": "通知公告",
                    },
                }
            ],
            "processingConfig": {"ocrEnabled": False, "chunkSize": 500, "chunkOverlap": 50},
            "saveAsDraft": False,
        },
    )

    assert ingestion.status_code == 202, ingestion.text
    task = ingestion.json()["data"]["items"][0]["task"]
    assert task["status"] == "failed"
    assert task["error"]["code"] == "TASK_DISPATCH_FAILED"
    assert "Redis" in task["error"]["action"] and "Celery" in task["error"]["action"]
    with SessionMaker() as db:
        stored = db.get(ProcessingTask, uuid.UUID(task["id"]))
        assert stored.status == "failed"
