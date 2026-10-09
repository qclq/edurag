from dataclasses import dataclass
import uuid

import jwt
from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import as_utc, decode_access_token, utcnow
from app.db.session import get_db
from app.modules.auth.models import AuthSession
from app.modules.users.models import Role, User


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentAuth:
    user: User
    session: AuthSession


def get_current_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentAuth:
    unauthorized = AppError(
        401,
        "AUTH_UNAUTHORIZED",
        "登录状态无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = decode_access_token(credentials.credentials, settings)
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if payload.get("type") != "access":
        raise unauthorized
    try:
        session_id = uuid.UUID(str(payload["sid"]))
        user_id = uuid.UUID(str(payload["sub"]))
        auth_session = db.scalar(select(AuthSession).where(AuthSession.id == session_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise unauthorized from exc
    if (
        auth_session is None
        or str(auth_session.user_id) != str(user_id)
        or auth_session.revoked_at is not None
        or as_utc(auth_session.expires_at) <= utcnow()
    ):
        raise unauthorized
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == auth_session.user_id)
    )
    if user is None or user.status != "active":
        raise unauthorized
    user.last_active_at = utcnow()
    db.commit()
    return CurrentAuth(user=user, session=auth_session)


def get_current_user(auth: CurrentAuth = Depends(get_current_auth)) -> User:
    return auth.user


def permission_codes(user: User) -> set[str]:
    return {permission.code for role in user.roles for permission in role.permissions}


def is_super_admin(user: User) -> bool:
    return any(role.code == "super_admin" for role in user.roles)


def require_permissions(*required: str, any_of: bool = False):
    """Create a dependency that enforces atomic RBAC permissions.

    Super administrators are deliberately granted an explicit bypass so a
    partially seeded permission catalog cannot lock out the recovery account.
    Resource-scope checks are still performed by the domain policy services.
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if is_super_admin(user):
            return user
        granted = permission_codes(user)
        allowed = bool(granted.intersection(required)) if any_of else set(required).issubset(granted)
        if not allowed:
            raise AppError(403, "PERMISSION_DENIED", "没有执行此操作的权限")
        return user

    return dependency


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def get_page_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)
