import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.security import utcnow
from app.modules.conversations.models import Message
from app.modules.operations.models import KnowledgeGap, QaArchiveMarker, QaArchiveSummary, QaRecord
from app.modules.operations.retention import run_admin_record_retention
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import create_task, update_task
from app.modules.users.models import User
from conftest import login


def _headers(client):
    response = login(client)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['accessToken']}"}


def _knowledge_base(client, headers):
    response = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={"name": "问答留存测试库", "businessCategory": "测试"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _ask(client, headers, kb_id):
    conversation = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"knowledgeBaseIds": [kb_id]},
    )
    assert conversation.status_code == 201, conversation.text
    response = client.post(
        f"/api/v1/conversations/{conversation.json()['data']['id']}/messages",
        headers=headers,
        json={"content": "测试知识库里没有答案的问题是什么？", "clientRequestId": "retention-test-question-001"},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]["assistantMessage"]["id"]


def test_admin_qa_records_archive_without_deleting_user_messages(test_context):
    client, SessionMaker, settings = test_context
    headers = _headers(client)
    kb = _knowledge_base(client, headers)
    message_id = _ask(client, headers, kb["id"])

    gaps = client.get("/api/v1/admin/knowledge-gaps?pageSize=20", headers=headers)
    assert gaps.status_code == 200, gaps.text
    gap = gaps.json()["data"][0]
    supplemented = client.post(
        f"/api/v1/admin/knowledge-gaps/{gap['id']}/supplement",
        headers=headers,
        json={"note": "已补充测试材料"},
    )
    assert supplemented.status_code == 200, supplemented.text

    now = utcnow()
    with SessionMaker() as db:
        record = db.scalar(select(QaRecord).where(QaRecord.source_message_id == uuid.UUID(message_id)))
        stored_gap = db.get(KnowledgeGap, uuid.UUID(gap["id"]))
        assert record is not None
        assert stored_gap is not None
        record.created_at = now - timedelta(days=4)
        stored_gap.supplemented_at = now - timedelta(days=4)
        db.commit()

    with SessionMaker() as db:
        result = run_admin_record_retention(db, settings, now=now)
        assert result["qaRecordsArchived"] == 1
        assert result["knowledgeGapsArchived"] == 1
        assert db.scalar(select(QaRecord).where(QaRecord.source_message_id == uuid.UUID(message_id))) is None
        assert db.get(QaArchiveMarker, uuid.UUID(message_id)) is not None
        assert db.get(Message, uuid.UUID(message_id)) is not None
        summary = db.scalar(select(QaArchiveSummary).where(QaArchiveSummary.knowledge_base_id == uuid.UUID(kb["id"])))
        assert summary is not None
        assert summary.question_count == 1
        assert summary.supplemented_gap_count == 1
        assert "共归档 1 条问答记录" in summary.summary_text

    with SessionMaker() as db:
        repeated = run_admin_record_retention(db, settings, now=now + timedelta(hours=1))
        assert repeated["backfilled"] == 0
        assert repeated["qaRecordsArchived"] == 0


def test_anomalies_are_human_readable_and_removed_with_knowledge_base(test_context):
    client, SessionMaker, _ = test_context
    headers = _headers(client)
    kb = _knowledge_base(client, headers)

    with SessionMaker() as db:
        user = db.scalar(select(User).where(User.account == "2026000001"))
        task, _ = create_task(
            db,
            type="document_reprocess",
            created_by=user.id,
            knowledge_base_id=uuid.UUID(kb["id"]),
        )
        update_task(
            db,
            task,
            status="failed",
            error_code="23503",
            error_message="pg8000 ProgrammingError foreign key constraint still referenced SQL DELETE",
        )
        db.commit()
        task_id = task.id

    response = client.get("/api/v1/admin/anomalies?pageSize=20", headers=headers)
    assert response.status_code == 200, response.text
    anomaly = response.json()["data"][0]
    assert anomaly["title"] == "文档重新处理未完成"
    assert "仍被现有问答引用" in anomaly["message"]
    assert "pg8000" not in str(anomaly)
    assert "SQL DELETE" not in str(anomaly)

    deleted = client.request(
        "DELETE",
        f"/api/v1/admin/knowledge-bases/{kb['id']}",
        headers=headers,
        json={"confirmName": kb["name"]},
    )
    assert deleted.status_code == 204, deleted.text
    with SessionMaker() as db:
        assert db.get(ProcessingTask, task_id) is None
        assert db.scalar(select(KnowledgeGap).where(KnowledgeGap.knowledge_base_id == uuid.UUID(kb["id"]))) is None
