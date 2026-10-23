import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import hash_password, verify_password
from app.integrations.embedding.provider import embed_texts
from app.integrations.llm.provider import generate_answer, plan_retrieval_queries
from app.integrations.storage import get_storage
from app.modules.conversations.models import MessageRating
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion, StoredFile
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import knowledge_base_code_from_name
from app.modules.users.models import Permission, Role, User
from conftest import captcha_value, login


def auth_headers(client):
    token = login(client).json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def create_super_admin_headers(client, SessionMaker, *, account: str, password: str = "SuperAdmin!2026"):
    with SessionMaker.begin() as db:
        super_role = db.scalar(select(Role).where(Role.code == "super_admin"))
        if super_role is None:
            super_role = Role(code="super_admin", name="超级管理员", is_system=True)
            db.add(super_role)
        super_user = User(
            account=account,
            password_hash=hash_password(password),
            name="超级管理员",
            user_type="staff",
            status="active",
            auth_source="local",
            roles=[super_role],
        )
        db.add(super_user)
        db.flush()
        user_id = super_user.id
    signed_in = login(client, password=password, account=account)
    assert signed_in.status_code == 200, signed_in.text
    headers = {"Authorization": f"Bearer {signed_in.json()['data']['accessToken']}"}
    return headers, user_id


def create_kb(client, headers):
    response = client.post(
        "/api/v1/admin/knowledge-bases",
        headers=headers,
        json={
            "name": "教务制度库",
            "description": "测试知识库",
            "businessCategory": "规章制度",
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


def test_knowledge_base_code_generation_uses_python_identifiers():
    assert knowledge_base_code_from_name("学生事务与奖助政策") == "xue_sheng_shi_wu_yu_jiang_zhu_zheng_ce"
    assert knowledge_base_code_from_name("Student Policy 2026") == "student_policy_2026"
    assert knowledge_base_code_from_name("2026 招生") == "kb_2026_zhao_sheng"
    assert knowledge_base_code_from_name("class") == "class_kb"
    assert knowledge_base_code_from_name("🎓") == "knowledge_base"


def test_knowledge_base_code_generation_resolves_duplicates(test_context):
    client, _, _ = test_context
    headers = auth_headers(client)

    first = client.post("/api/v1/admin/knowledge-bases", headers=headers, json={"name": "学生政策"})
    second = client.post("/api/v1/admin/knowledge-bases", headers=headers, json={"name": "学生政策"})

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["data"]["code"] == "xue_sheng_zheng_ce"
    assert second.json()["data"]["code"] == "xue_sheng_zheng_ce_2"


def test_knowledge_base_and_draft_ingestion_flow(test_context):
    client, _, _ = test_context
    headers = auth_headers(client)
    denied_users = client.get("/api/v1/admin/users", headers=headers)
    assert denied_users.status_code == 403
    assert denied_users.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"
    kb = create_kb(client, headers)

    options = client.get("/api/v1/knowledge-bases/options", headers=headers)
    assert options.status_code == 200
    assert options.json()["data"][0]["code"] == "jiao_wu_zhi_du_ku"
    assert options.json()["data"][0]["category"] == "规章制度"

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

    mismatch = client.request(
        "DELETE",
        f"/api/v1/admin/documents/{document['id']}",
        headers=headers,
        json={"confirmName": "错误名称"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "CONFIRMATION_MISMATCH"

    deleted = client.request(
        "DELETE",
        f"/api/v1/admin/documents/{document['id']}",
        headers=headers,
        json={"confirmName": document["displayName"]},
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/admin/documents/{document['id']}", headers=headers).status_code == 404
    remaining = client.get("/api/v1/admin/documents?pageSize=100", headers=headers)
    assert all(item["id"] != document["id"] for item in remaining.json()["data"])

    wrong_kb_name = client.request(
        "DELETE",
        f"/api/v1/admin/knowledge-bases/{kb['id']}",
        headers=headers,
        json={"confirmName": "错误知识库名称"},
    )
    assert wrong_kb_name.status_code == 400
    assert wrong_kb_name.json()["error"]["code"] == "CONFIRMATION_MISMATCH"

    deleted_kb = client.request(
        "DELETE",
        f"/api/v1/admin/knowledge-bases/{kb['id']}",
        headers=headers,
        json={"confirmName": kb["name"]},
    )
    assert deleted_kb.status_code == 204
    remaining_kbs = client.get("/api/v1/admin/knowledge-bases?pageSize=100", headers=headers)
    assert all(item["id"] != kb["id"] for item in remaining_kbs.json()["data"])


def test_conversation_message_rag_and_rating(test_context):
    client, SessionMaker, settings = test_context
    headers = auth_headers(client)
    kb_data = create_kb(client, headers)
    kb_id = uuid.UUID(kb_data["id"])
    original_file = "国家奖学金申请人须提交成绩单。".encode()
    get_storage(settings).put("tests/rules.txt", original_file, "text/plain")

    with SessionMaker.begin() as db:
        user = db.scalar(select(User).where(User.account == "2026000001"))
        stored = StoredFile(
            storage_key="tests/rules.txt",
            original_name="奖学金办法.txt",
            mime_type="text/plain",
            size_bytes=len(original_file),
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
        json={},
    )
    assert conversation.status_code == 201, conversation.text
    assert conversation.json()["data"]["knowledgeBases"][0]["id"] == str(kb_id)
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
    assert assistant["citations"][0]["documentName"] == "奖学金管理办法"
    assert assistant["citations"][0]["downloadUrl"] == f"/api/v1/citations/{assistant['citations'][0]['id']}/download"
    assert assistant["downloadableDocuments"] == [
        {
            "documentId": str(document.id),
            "documentName": "奖学金管理办法",
            "fileName": "奖学金办法.txt",
            "downloadUrl": f"/api/v1/citations/{assistant['citations'][0]['id']}/download",
        }
    ]
    downloaded = client.get(assistant["downloadableDocuments"][0]["downloadUrl"], headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == original_file
    assert "attachment" in downloaded.headers["content-disposition"]

    rating = client.put(
        f"/api/v1/messages/{assistant['id']}/rating",
        headers=headers,
        json={"value": "unhelpful", "reason": "missing_detail", "comment": "还缺截止日期"},
    )
    assert rating.status_code == 200
    assert rating.json()["data"]["feedbackId"]

    repeated_rating = client.put(
        f"/api/v1/messages/{assistant['id']}/rating",
        headers=headers,
        json={"value": "unhelpful"},
    )
    assert repeated_rating.status_code == 200
    conversation_detail = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert conversation_detail.status_code == 200
    rated_answer = next(
        item for item in conversation_detail.json()["data"]["messages"]
        if item["id"] == assistant["id"]
    )
    assert rated_answer["rating"]["value"] == "unhelpful"
    assert rated_answer["citations"][0]["documentName"] == "奖学金管理办法"
    assert rated_answer["downloadableDocuments"][0]["fileName"] == "奖学金办法.txt"
    with SessionMaker() as db:
        saved_ratings = db.scalars(
            select(MessageRating).where(MessageRating.message_id == uuid.UUID(assistant["id"]))
        ).all()
        assert len(saved_ratings) == 1

    reprocess = client.post(
        f"/api/v1/admin/documents/{document.id}/reprocess",
        headers=headers,
        json={
            "reason": "调整切片后重新处理",
            "ocrEnabled": False,
            "chunkSize": 600,
            "chunkOverlap": 80,
        },
    )
    assert reprocess.status_code == 202, reprocess.text
    with SessionMaker() as db:
        versions = db.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.version_no)
        ).all()
        assert len(versions) == 2
        assert versions[0].is_current is True
        assert versions[0].process_status == "ready"
        assert versions[1].is_current is False
        assert versions[1].process_status == "pending"
        assert versions[1].file_id == versions[0].file_id


def test_normal_user_can_download_document_cited_in_own_answer(test_context):
    client, SessionMaker, settings = test_context
    admin_headers = auth_headers(client)
    kb_data = create_kb(client, admin_headers)
    kb_id = uuid.UUID(kb_data["id"])
    original_file = "普通用户也可以下载问答引用的原文。".encode()
    get_storage(settings).put("tests/cited-source.txt", original_file, "text/plain")

    with SessionMaker.begin() as db:
        normal_role = db.scalar(select(Role).where(Role.code == "normal_user"))
        uploader = db.scalar(select(User).where(User.account == "2026000001"))
        reader = User(
            account="citation-reader",
            password_hash=hash_password("ReaderPass!2026"),
            name="普通问答用户",
            user_type="student",
            status="active",
            auth_source="local",
            roles=[normal_role],
        )
        db.add(reader)
        stored = StoredFile(
            storage_key="tests/cited-source.txt",
            original_name="问答引用原文.txt",
            mime_type="text/plain",
            size_bytes=len(original_file),
            sha256="c" * 64,
            scan_status="clean",
            uploaded_by=uploader.id,
        )
        db.add(stored)
        db.flush()
        document = Document(
            knowledge_base_id=kb_id,
            display_name="问答引用原文",
            status="ready",
            enabled=True,
            uploaded_by=uploader.id,
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
                content="普通用户在问答后可以下载回答所引用的相关原文件。",
                search_tokens="普通用户 问答 下载 引用 原文件",
                token_count=16,
                vector_status="ready",
                content_hash="d" * 64,
                section="下载说明",
            )
        )
        document_id = document.id

    signed_in = login(client, password="ReaderPass!2026", account="citation-reader")
    assert signed_in.status_code == 200, signed_in.text
    reader_headers = {"Authorization": f"Bearer {signed_in.json()['data']['accessToken']}"}
    conversation = client.post("/api/v1/conversations", headers=reader_headers, json={})
    assert conversation.status_code == 201, conversation.text
    answer = client.post(
        f"/api/v1/conversations/{conversation.json()['data']['id']}/messages",
        headers=reader_headers,
        json={"content": "普通用户能下载问答引用的原文件吗？", "clientRequestId": "reader-download-0001"},
    )
    assert answer.status_code == 202, answer.text
    citation = answer.json()["data"]["assistantMessage"]["citations"][0]

    # The broad document endpoint remains ACL-protected; the answer's citation
    # link is the proof that this user may download this specific source.
    assert client.get(f"/api/v1/documents/{document_id}/download", headers=reader_headers).status_code == 404
    downloaded = client.get(citation["downloadUrl"], headers=reader_headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == original_file
    assert "attachment" in downloaded.headers["content-disposition"]
    assert client.get(citation["downloadUrl"], headers=admin_headers).status_code == 404


def test_admin_student_create_forces_first_login_password_change(test_context):
    client, SessionMaker, _ = test_context
    headers, _ = create_super_admin_headers(client, SessionMaker, account="student-admin")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "name": "测试学生",
            "studentOrStaffNo": "2026999999",
            "userType": "student",
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()["data"]
    assert user["account"] == "2026999999"
    assert user["mustChangePassword"] is True
    assert [role["code"] for role in user["roles"]] == ["normal_user"]

    with SessionMaker() as db:
        stored = db.scalar(select(User).where(User.account == "2026999999"))
        assert stored is not None
        assert stored.user_type == "student"
        assert stored.must_change_password is True
        assert verify_password("123456", stored.password_hash)

    captcha_id, captcha_code = captcha_value(client)
    logged_in = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026999999",
            "password": "123456",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
            "remember": True,
        },
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["data"]["user"]["mustChangePassword"] is True
    student_headers = {"Authorization": f"Bearer {logged_in.json()['data']['accessToken']}"}
    blocked = client.get("/api/v1/conversations", headers=student_headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "AUTH_PASSWORD_CHANGE_REQUIRED"

    changed = client.post(
        "/api/v1/me/password",
        headers=student_headers,
        json={"newPassword": "NewPassword!2026"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["mustChangePassword"] is False
    current_user = client.get("/api/v1/auth/me", headers=student_headers)
    assert current_user.status_code == 200, current_user.text
    assert current_user.json()["data"]["mustChangePassword"] is False

    # A mistakenly granted atomic permission must not turn a student into a
    # super administrator capable of resetting their own forgotten password.
    with SessionMaker() as db:
        normal_role = db.scalar(select(Role).where(Role.code == "normal_user"))
        reset_permission = db.scalar(select(Permission).where(Permission.code == "user.password.reset"))
        normal_role.permissions.append(reset_permission)
        db.commit()

    self_reset = client.post(
        f"/api/v1/admin/users/{user['id']}/password-reset",
        headers=student_headers,
        json={"deliveryMethod": "manual"},
    )
    assert self_reset.status_code == 403
    assert self_reset.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"

    admin_reset = client.post(
        f"/api/v1/admin/users/{user['id']}/password-reset",
        headers=headers,
        json={"deliveryMethod": "manual"},
    )
    assert admin_reset.status_code == 200, admin_reset.text
    assert admin_reset.json()["data"]["status"] == "reset_to_default"
    assert admin_reset.json()["data"]["mustChangePassword"] is True

    with SessionMaker() as db:
        stored = db.scalar(select(User).where(User.account == "2026999999"))
        assert stored.must_change_password is True
        assert verify_password("123456", stored.password_hash)

    disabled = client.post(
        f"/api/v1/admin/users/{user['id']}/status",
        headers=headers,
        json={"status": "disabled", "reason": "测试停用"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["status"] == "disabled"


def test_staff_roles_and_external_history_policy(test_context):
    client, SessionMaker, _ = test_context
    admin_headers = auth_headers(client)

    denied_admin_staff = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "name": "越权管理员",
            "account": "staff-admin-denied",
            "userType": "staff",
            "roleCodes": ["knowledge_admin"],
        },
    )
    assert denied_admin_staff.status_code == 403
    assert denied_admin_staff.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"

    with SessionMaker() as db:
        super_role = Role(code="super_admin", name="超级管理员", is_system=True)
        super_user = User(
            account="account-super-admin",
            password_hash=hash_password("SuperAdmin!2026"),
            name="账号超级管理员",
            user_type="staff",
            status="active",
            auth_source="local",
            roles=[super_role],
        )
        db.add(super_user)
        db.commit()

    with TestClient(client.app) as super_client:
        signed_in = login(super_client, password="SuperAdmin!2026", account="account-super-admin")
        assert signed_in.status_code == 200, signed_in.text
        super_headers = {"Authorization": f"Bearer {signed_in.json()['data']['accessToken']}"}
        created_staff = super_client.post(
            "/api/v1/admin/users",
            headers=super_headers,
            json={
                "name": "教职工管理员",
                "account": "staff-admin",
                "studentOrStaffNo": "T20260001",
                "userType": "staff",
                "roleCodes": ["knowledge_admin"],
            },
        )
        assert created_staff.status_code == 201, created_staff.text
        staff = created_staff.json()["data"]
        assert staff["mustChangePassword"] is True
        assert [role["code"] for role in staff["roles"]] == ["knowledge_admin"]
        denied_external = super_client.post(
            "/api/v1/admin/users",
            headers=super_headers,
            json={"name": "不应创建的访客", "account": "visitor-denied", "userType": "external"},
        )
        assert denied_external.status_code == 400
        assert denied_external.json()["error"]["code"] == "EXTERNAL_USER_CREATION_DISABLED"

    blocked_admin_status = client.post(
        f"/api/v1/admin/users/{staff['id']}/status",
        headers=admin_headers,
        json={"status": "disabled", "reason": "普通管理员不应管理管理员账号"},
    )
    assert blocked_admin_status.status_code == 403
    assert blocked_admin_status.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"
    blocked_admin_reset = client.post(
        f"/api/v1/admin/users/{staff['id']}/password-reset",
        headers=admin_headers,
        json={"deliveryMethod": "manual"},
    )
    assert blocked_admin_reset.status_code == 403
    assert blocked_admin_reset.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"

    create_kb(client, admin_headers)
    denied_external = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"name": "不应创建的访客", "account": "visitor-denied-2", "userType": "external"},
    )
    assert denied_external.status_code == 403
    assert denied_external.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"

    with TestClient(client.app) as external_client:
        accessed = external_client.post(
            "/api/v1/auth/external-access",
        )
        assert accessed.status_code == 200, accessed.text
        external = accessed.json()["data"]["user"]
        assert external["userType"] == "external"
        assert external["authSource"] == "guest"
        assert external["mustChangePassword"] is False
        assert [role["code"] for role in external["roles"]] == ["normal_user"]
        external_headers = {"Authorization": f"Bearer {accessed.json()['data']['accessToken']}"}

        avatar_update = external_client.post(
            "/api/v1/me/avatar",
            headers=external_headers,
            files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert avatar_update.status_code == 403
        assert avatar_update.json()["error"]["code"] == "EXTERNAL_PROFILE_UPDATE_FORBIDDEN"

        conversation = external_client.post(
            "/api/v1/conversations",
            headers=external_headers,
            json={"title": "外部人员咨询"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = conversation.json()["data"]["id"]
        submitted = external_client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=external_headers,
            json={"content": "如何办理校园访客手续？", "clientRequestId": "external-question-001"},
        )
        assert submitted.status_code == 202, submitted.text
        assistant_id = submitted.json()["data"]["assistantMessage"]["id"]
        rated = external_client.put(
            f"/api/v1/messages/{assistant_id}/rating",
            headers=external_headers,
            json={"value": "unhelpful", "comment": "没有找到具体办理地点"},
        )
        assert rated.status_code == 200, rated.text

        history = external_client.get("/api/v1/conversations", headers=external_headers)
        assert history.status_code == 403
        assert history.json()["error"]["code"] == "EXTERNAL_HISTORY_FORBIDDEN"
        rename = external_client.patch(
            f"/api/v1/conversations/{conversation_id}",
            headers=external_headers,
            json={"title": "不应允许改名"},
        )
        assert rename.status_code == 403
        search = external_client.get("/api/v1/search?q=访客", headers=external_headers)
        assert search.status_code == 403

    qa_records = client.get("/api/v1/admin/qa-records?pageSize=100", headers=admin_headers)
    assert qa_records.status_code == 200, qa_records.text
    assert all(item["userId"] != external["id"] for item in qa_records.json()["data"])
    feedbacks = client.get("/api/v1/admin/feedbacks?pageSize=100", headers=admin_headers)
    assert feedbacks.status_code == 200, feedbacks.text
    assert all(item["createdBy"] != external["id"] for item in feedbacks.json()["data"])

    with TestClient(client.app) as super_client:
        signed_in = login(super_client, password="SuperAdmin!2026", account="account-super-admin")
        assert signed_in.status_code == 200, signed_in.text
        super_headers = {"Authorization": f"Bearer {signed_in.json()['data']['accessToken']}"}
        qa_records = super_client.get("/api/v1/admin/qa-records?pageSize=100", headers=super_headers)
        assert qa_records.status_code == 200, qa_records.text
        assert any(item["userId"] == external["id"] for item in qa_records.json()["data"])
        feedbacks = super_client.get("/api/v1/admin/feedbacks?pageSize=100", headers=super_headers)
        assert feedbacks.status_code == 200, feedbacks.text
        assert any(item["createdBy"] == external["id"] for item in feedbacks.json()["data"])

    listed_users = client.get("/api/v1/admin/users?pageSize=100", headers=admin_headers)
    assert listed_users.status_code == 403
    assert listed_users.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"


def test_only_super_admin_can_delete_user(test_context):
    client, SessionMaker, _ = test_context
    admin_headers = auth_headers(client)
    super_client = TestClient(client.app)
    super_headers, super_user_id = create_super_admin_headers(
        super_client,
        SessionMaker,
        account="root-admin",
    )
    created = super_client.post(
        "/api/v1/admin/users",
        headers=super_headers,
        json={"name": "待删除学生", "studentOrStaffNo": "2026777777", "userType": "student"},
    )
    assert created.status_code == 201, created.text
    target = created.json()["data"]

    denied = client.delete(f"/api/v1/admin/users/{target['id']}", headers=admin_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "SUPER_ADMIN_REQUIRED"

    self_delete = super_client.delete(f"/api/v1/admin/users/{super_user_id}", headers=super_headers)
    assert self_delete.status_code == 409
    assert self_delete.json()["error"]["code"] == "USER_SELF_DELETE_FORBIDDEN"

    deleted = super_client.delete(f"/api/v1/admin/users/{target['id']}", headers=super_headers)
    assert deleted.status_code == 204, deleted.text
    assert super_client.get(f"/api/v1/admin/users/{target['id']}", headers=super_headers).status_code == 404

    with SessionMaker() as db:
        stored = db.get(User, uuid.UUID(target["id"]))
        assert stored.status == "deleted"
        assert stored.account.startswith("deleted-")
        assert stored.name == "已删除用户"
        assert stored.student_or_staff_no is None
        assert stored.password_hash is None


def test_student_import_reads_only_name_and_student_number(test_context):
    client, SessionMaker, _ = test_context
    headers, _ = create_super_admin_headers(client, SessionMaker, account="import-admin")

    invalid = client.post(
        "/api/v1/admin/user-imports/validate",
        headers=headers,
        files={"file": ("missing.csv", "姓名,邮箱\n王同学,ignored@example.com".encode(), "text/csv")},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "USER_IMPORT_HEADERS_INVALID"

    validated = client.post(
        "/api/v1/admin/user-imports/validate",
        headers=headers,
        files={
            "file": (
                "students.csv",
                "姓名,学号,邮箱,角色\n王同学,2026888888,ignored@example.com,super_admin".encode(),
                "text/csv",
            )
        },
    )
    assert validated.status_code == 201, validated.text
    review = validated.json()["data"]
    assert review["summary"] == {"total": 1, "valid": 1, "conflicts": 0, "errors": 0}

    committed = client.post(
        f"/api/v1/admin/user-imports/{review['importId']}/commit",
        headers=headers,
        json={"decisions": []},
    )
    assert committed.status_code == 202, committed.text
    assert committed.json()["data"]["result"]["created"] == 1

    with SessionMaker() as db:
        student = db.scalar(select(User).where(User.account == "2026888888"))
        assert student is not None
        assert student.name == "王同学"
        assert student.email is None
        assert student.user_type == "student"
        assert student.must_change_password is True
        assert verify_password("123456", student.password_hash)
        assert [role.code for role in student.roles] == ["normal_user"]


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
        if "检索规划器" in kwargs["json"]["messages"][0]["content"]:
            return FakeResponse({"choices": [{"message": {"content": '{"queries":["国家奖学金申请材料","成绩单排名要求"]}'}}]})
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
    queries = plan_retrieval_queries("国家奖学金申请需要什么？", settings)
    answer, model = generate_answer("需要什么？", [{"documentName": "办法", "content": "提交成绩单"}], settings)
    assert len(vectors[0]) == 1024
    assert answer == "请提交成绩单。[1]"
    assert model == "deepseek-flash"
    assert queries == ["国家奖学金申请材料", "成绩单排名要求", "国家奖学金申请需要什么？"]
    assert calls[0][1]["headers"]["Authorization"] == "Bearer qwen-secret"
    assert calls[1][1]["headers"]["Authorization"] == "Bearer deepseek-secret"
    assert calls[2][1]["headers"]["Authorization"] == "Bearer deepseek-secret"
