import uuid

from sqlalchemy import select

from app.core.config import Settings
from app.integrations.embedding.provider import embed_texts
from app.integrations.llm.provider import generate_answer
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, StoredFile
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.users.models import User
from conftest import login


def auth_headers(client):
    token = login(client).json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def create_kb(client, headers):
    response = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={
            "name": "教务制度库",
            "code": "ACADEMIC_RULES",
            "description": "测试知识库",
            "acl": [{"isPublic": True, "canSearch": True, "canPreview": True}],
            "retrieval": {
                "chunkSize": 500,
                "chunkOverlap": 50,
                "topK": 5,
                "similarityThreshold": 0,
                "rerankEnabled": True,
                "hybridSearchEnabled": True,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_knowledge_base_and_draft_ingestion_flow(test_context):
    client, _, _ = test_context
    headers = auth_headers(client)
    kb = create_kb(client, headers)

    options = client.get("/api/v1/knowledge-bases/options", headers=headers)
    assert options.status_code == 200
    assert options.json()["data"][0]["code"] == "ACADEMIC_RULES"

    upload = client.post(
        "/api/v1/admin/uploads",
        headers=headers,
        data={"clientFileId": "client-file-001"},
        files={"file": ("guide.txt", "奖学金申请需要提交成绩单。".encode(), "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    upload_id = upload.json()["data"]["uploadId"]

    ingestion = client.post(
        "/api/v1/admin/document-ingestions",
        headers=headers,
        json={
            "items": [
                {
                    "uploadId": upload_id,
                    "metadata": {
                        "knowledgeBaseId": kb["id"],
                        "displayName": "奖学金申请指南",
                        "category": "办事指南",
                        "tags": ["奖学金"],
                    },
                }
            ],
            "processingConfig": {"ocrEnabled": False, "chunkSize": 500, "chunkOverlap": 50},
            "saveAsDraft": True,
        },
    )
    assert ingestion.status_code == 201, ingestion.text
    document = ingestion.json()["data"]["items"][0]["document"]
    assert document["status"] == "draft"
    assert document["tags"] == ["奖学金"]


def test_conversation_message_rag_and_rating(test_context):
    client, SessionMaker, _ = test_context
    headers = auth_headers(client)
    kb_data = create_kb(client, headers)
    kb_id = uuid.UUID(kb_data["id"])

    with SessionMaker.begin() as db:
        user = db.scalar(select(User).where(User.account == "2026000001"))
        stored = StoredFile(
            storage_key="tests/rules.txt",
            original_name="奖学金办法.txt",
            mime_type="text/plain",
            size_bytes=20,
            sha256="a" * 64,
            scan_status="clean",
            uploaded_by=user.id,
        )
        db.add(stored)
        db.flush()
        document = Document(
            knowledge_base_id=kb_id,
            display_name="奖学金管理办法",
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
        )
        db.add(version)
        db.flush()
        document.current_version_id = version.id
        db.add(
            DocumentChunk(
                document_version_id=version.id,
                chunk_index=0,
                content="国家奖学金申请人须提交成绩单，并满足学业成绩排名要求。",
                search_tokens="国家奖学金 申请 成绩单 学业成绩 排名",
                token_count=20,
                vector_status="ready",
                content_hash="b" * 64,
                page_start=3,
                page_end=3,
                section="申请条件",
            )
        )

    conversation = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"knowledgeBaseIds": [str(kb_id)]},
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["data"]["id"]

    answer = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": "国家奖学金申请需要什么？", "clientRequestId": "request-00000001"},
    )
    assert answer.status_code == 202, answer.text
    assistant = answer.json()["data"]["assistantMessage"]
    assert assistant["status"] == "completed"
    assert assistant["citations"][0]["pageStart"] == 3

    rating = client.put(
        f"/api/v1/messages/{assistant['id']}/rating",
        headers=headers,
        json={"value": "unhelpful", "reason": "missing_detail", "comment": "还缺截止日期"},
    )
    assert rating.status_code == 200
    assert rating.json()["data"]["feedbackId"]


def test_admin_user_create_and_status(test_context):
    client, _, _ = test_context
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "name": "测试学生",
            "account": "2026999999",
            "studentOrStaffNo": "2026999999",
            "userType": "student",
            "roleCodes": ["normal_user"],
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()["data"]
    disabled = client.post(
        f"/api/v1/admin/users/{user['id']}/status",
        headers=headers,
        json={"status": "disabled", "reason": "测试停用"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "disabled"


def test_qwen_and_deepseek_clients_have_independent_configuration(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/embeddings"):
            return FakeResponse({"data": [{"index": 0, "embedding": [0.0] * 1024}]})
        return FakeResponse({"choices": [{"message": {"content": "请提交成绩单。[1]"}}]})

    monkeypatch.setattr("httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        environment="test",
        secret_key="test-secret-key-that-is-long-enough-for-tests",
        qwen_api_key="qwen-secret",
        deepseek_api_key="deepseek-secret",
        milvus_enabled=False,
    )
    vectors = embed_texts(["奖学金"], settings)
    answer, model = generate_answer("需要什么？", [{"documentName": "办法", "content": "提交成绩单"}], settings)
    assert len(vectors[0]) == 1024
    assert answer == "请提交成绩单。[1]"
    assert model == "deepseek-flash"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer qwen-secret"
    assert calls[1][1]["headers"]["Authorization"] == "Bearer deepseek-secret"
