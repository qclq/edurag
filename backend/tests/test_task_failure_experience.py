from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from conftest import login
from app.core.config import Settings
from app.integrations.ocr.provider import OcrProviderError, ocr_image
from app.modules.conversations.models import Conversation, Message
from app.modules.documents.models import DocumentVersion, StoredFile
from app.modules.feedback.models import Feedback
from app.modules.tasks.models import ProcessingTask
from app.modules.tasks.service import task_payload
from app.modules.users.models import User


def test_task_payload_exposes_original_file_and_actionable_failure() -> None:
    stored_file = StoredFile(
        storage_key="tests/policy.XLS",
        original_name="国家奖学金名单（原稿）.XLS",
        mime_type="application/vnd.ms-excel",
        size_bytes=128,
        sha256="a" * 64,
        scan_status="clean",
    )
    version = DocumentVersion(version_no=1, file=stored_file)
    task = ProcessingTask(
        task_no="TASK-TEST-OCR",
        type="document_ingestion",
        status="failed",
        stage="ocr",
        progress_text="文档处理失败",
        error_code="OCR_PROVIDER_ERROR",
        error_message="Qwen OCR 图片识别失败",
    )
    task.document_version = version

    payload = task_payload(task, include_events=False)

    assert payload["fileName"] == "国家奖学金名单（原稿）.XLS"
    assert payload["fileExtension"] == "XLS"
    assert payload["error"]["message"] == "Qwen OCR 图片识别失败"
    assert "Qwen OCR" in payload["error"]["action"]
    assert payload["error"]["retryable"] is True


def test_qwen_ocr_404_reports_model_or_endpoint_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "https://dashscope.example/compatible-mode/v1/chat/completions")

    def fake_post(*args, **kwargs):
        return httpx.Response(404, request=request, json={"error": {"message": "not found"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        qwen_api_key="test-key",
        qwen_ocr_base_url="https://dashscope.example/compatible-mode/v1",
        qwen_ocr_model="qwen-vl-ocr-latest",
    )

    with pytest.raises(OcrProviderError) as caught:
        ocr_image(b"png", "image/png", settings)

    assert caught.value.code == "OCR_PROVIDER_NOT_FOUND"
    assert "404" in str(caught.value)
    assert "模型" in str(caught.value) or "接口" in str(caught.value)


def test_authenticated_user_can_submit_feedback(test_context) -> None:
    client, testing_session, _ = test_context
    signed_in = login(client)
    headers = {"Authorization": f"Bearer {signed_in.json()['data']['accessToken']}"}

    response = client.post(
        "/api/v1/feedbacks",
        headers=headers,
        json={"type": "bug", "comment": "任务中心看不到失败原因", "pagePath": "/admin/upload"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["type"] == "user_bug"
    with testing_session() as db:
        feedback_id = uuid.UUID(response.json()["data"]["id"])
        feedback = db.scalar(select(Feedback).where(Feedback.id == feedback_id))
        assert feedback is not None
        assert "任务中心看不到失败原因" in feedback.comment
        assert "页面：/admin/upload" in feedback.comment


def test_conversation_feedback_is_bound_to_owned_answer(test_context) -> None:
    client, testing_session, _ = test_context
    with testing_session.begin() as db:
        user = db.scalar(select(User).where(User.account == "2026000001"))
        conversation = Conversation(user_id=user.id, title="奖学金咨询")
        db.add(conversation)
        db.flush()
        answer = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="申请条件如下",
            status="completed",
        )
        db.add(answer)
        db.flush()
        conversation_id = conversation.id
        message_id = answer.id

    signed_in = login(client)
    headers = {"Authorization": f"Bearer {signed_in.json()['data']['accessToken']}"}
    response = client.post(
        "/api/v1/feedbacks",
        headers=headers,
        json={
            "type": "answer_incorrect",
            "comment": "申请年级写错了",
            "conversationId": str(conversation_id),
            "messageId": str(message_id),
            "pagePath": f"/chat?conversationId={conversation_id}",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["type"] == "conversation_answer_incorrect"
    with testing_session() as db:
        feedback = db.get(Feedback, uuid.UUID(response.json()["data"]["id"]))
        assert feedback.conversation_id == conversation_id
        assert feedback.message_id == message_id
