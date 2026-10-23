from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import AppError, success
from app.db.session import get_db
from app.modules.conversations.service import get_conversation_or_404, get_message_or_404
from app.modules.feedback.models import Feedback
from app.modules.feedback.schemas import UserFeedbackCreate
from app.modules.operations.service import record_audit
from app.modules.users.models import User


router = APIRouter()


@router.post("/feedbacks", status_code=201, summary="提交用户反馈")
def create_user_feedback(
    payload: UserFeedbackCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = None
    message = None
    if payload.message_id:
        message = get_message_or_404(db, payload.message_id, user)
        conversation = message.conversation
        if payload.conversation_id and message.conversation_id != payload.conversation_id:
            raise AppError(400, "FEEDBACK_SCOPE_INVALID", "反馈关联的对话与回答不一致")
    elif payload.conversation_id:
        conversation = get_conversation_or_404(db, payload.conversation_id, user)

    comment = payload.comment.strip()
    if payload.page_path:
        comment = f"{comment}\n\n页面：{payload.page_path}"
    feedback = Feedback(
        created_by=user.id,
        type=f"conversation_{payload.type}" if conversation else f"user_{payload.type}",
        comment=comment,
        conversation_id=conversation.id if conversation else None,
        message_id=message.id if message else None,
        knowledge_base_id=(
            conversation.knowledge_bases[0].id
            if conversation and conversation.knowledge_bases
            else None
        ),
    )
    db.add(feedback)
    db.flush()
    record_audit(
        db,
        actor=user,
        module="feedback",
        action="create",
        target_type="feedback",
        target_id=feedback.id,
        request=request,
        after={
            "type": feedback.type,
            "pagePath": payload.page_path,
            "conversationId": str(conversation.id) if conversation else None,
            "messageId": str(message.id) if message else None,
        },
    )
    db.commit()
    return success(
        request,
        {
            "id": str(feedback.id),
            "type": feedback.type,
            "status": feedback.status,
            "createdAt": feedback.created_at,
        },
        status_code=201,
    )
