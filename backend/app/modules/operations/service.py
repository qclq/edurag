from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.modules.operations.models import AuditLog, Notification
from app.modules.users.models import User


def record_audit(
    db: Session,
    *,
    actor: User | None,
    module: str,
    action: str,
    target_type: str | None = None,
    target_id: object | None = None,
    request: Request | None = None,
    before: Any = None,
    after: Any = None,
    result: str = "success",
) -> AuditLog:
    log = AuditLog(
        actor_id=actor.id if actor else None,
        module=module,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        result=result,
        ip=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500] if request else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
        before_json=jsonable_encoder(before) if before is not None else None,
        after_json=jsonable_encoder(after) if after is not None else None,
    )
    db.add(log)
    return log


def notify(
    db: Session,
    user_id,
    *,
    type: str,
    title: str,
    body: str,
    target_type: str | None = None,
    target_id=None,
    batch_key: str | None = None,
) -> Notification:
    item = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        target_type=target_type,
        target_id=target_id,
        batch_key=batch_key,
    )
    db.add(item)
    return item
