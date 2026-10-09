from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.modules.conversations.models import Conversation, Message
from app.modules.conversations.service import generate_message
from app.modules.users.models import Role, User


def run_answer_generation(message_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    with SessionLocal() as db:
        message = db.scalar(select(Message).where(Message.id == uuid.UUID(message_id)))
        if not message or message.status in {"completed", "no_answer", "stopped", "failed"}:
            return
        conversation = db.get(Conversation, message.conversation_id)
        if not conversation:
            return
        user = db.scalar(
            select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == conversation.user_id)
        )
        if user:
            generate_message(db, message, user, settings)


def dispatch_answer_generation(message_id: str, settings: Settings) -> None:
    if settings.task_dispatch_mode == "inline":
        run_answer_generation(message_id, settings)
        return
    generate_answer_task.delay(message_id)


try:
    from app.workers.celery_app import celery_app

    @celery_app.task(name="edurag.generate_answer", autoretry_for=(), max_retries=0)
    def generate_answer_task(message_id: str) -> None:
        run_answer_generation(message_id)
except ImportError:
    class _UnavailableTask:
        def delay(self, message_id: str) -> None:
            raise RuntimeError("Celery 未安装，无法派发后台任务")

    generate_answer_task = _UnavailableTask()
