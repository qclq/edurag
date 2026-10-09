from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_password, utcnow, verify_password
from app.modules.auth.models import AuthSession, LoginEvent
from app.modules.users.models import User
from app.modules.users.schemas import PasswordChange, ProfileUpdate


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return value
    local, domain = value.split("@", 1)
    return f"{local[:1]}***@{domain}"


def mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 7:
        return f"{value[:1]}***{value[-1:]}"
    return f"{value[:3]}****{value[-4:]}"


def user_payload(user: User, db: Session) -> dict[str, object]:
    role_items = sorted(
        ({"code": role.code, "name": role.name} for role in user.roles), key=lambda item: str(item["code"])
    )
    permissions = sorted({permission.code for role in user.roles for permission in role.permissions})
    recent_event = db.scalar(
        select(LoginEvent)
        .where(LoginEvent.user_id == user.id, LoginEvent.result == "success")
        .order_by(LoginEvent.occurred_at.desc())
        .limit(1)
    )
    admin_roles = {"knowledge_admin", "user_admin", "super_admin"}
    admin_permission_prefixes = (
        "dashboard.", "kb.", "document.", "chunk.", "task.", "user.",
        "role.", "qa.", "feedback.", "system.", "audit.",
    )
    read_only = ["account", "name", "studentOrStaffNo", "userType", "department", "status", "authSource"]
    if user.auth_source != "local":
        read_only.extend(["email", "phone"])
    return {
        "id": str(user.id),
        "account": user.account,
        "name": user.name,
        "studentOrStaffNo": user.student_or_staff_no,
        "userType": user.user_type,
        "subtitle": user.subtitle,
        "department": (
            {"id": str(user.department.id), "code": user.department.code, "name": user.department.name}
            if user.department
            else None
        ),
        "email": mask_email(user.email),
        "phone": mask_phone(user.phone),
        "avatarUrl": user.avatar_url,
        "status": user.status,
        "authSource": user.auth_source,
        "roles": role_items,
        "permissions": permissions,
        "managementScopes": {"departmentIds": [], "knowledgeBaseIds": []},
        "canAccessAdmin": bool(
            admin_roles.intersection(role.code for role in user.roles)
            or any(code.startswith(admin_permission_prefixes) for code in permissions)
        ),
        "readOnlyFields": sorted(set(read_only)),
        "lastLoginAt": user.last_login_at,
        "lastActiveAt": user.last_active_at,
        "recentLogin": (
            {
                "occurredAt": recent_event.occurred_at,
                "ip": recent_event.ip,
                "client": recent_event.user_agent,
            }
            if recent_event
            else None
        ),
        "createdAt": user.created_at,
        "updatedAt": user.updated_at,
    }


def update_profile(db: Session, user: User, payload: ProfileUpdate) -> User:
    supplied = payload.model_fields_set
    synchronized_fields = supplied.intersection({"email", "phone"})
    if user.auth_source != "local" and synchronized_fields:
        raise AppError(
            409,
            "PROFILE_FIELD_MANAGED_EXTERNALLY",
            "手机号和邮箱由统一身份认证同步，不能在本系统修改",
            details={"fields": sorted(synchronized_fields)},
        )
    if "email" in supplied:
        user.email = str(payload.email) if payload.email else None
    if "phone" in supplied:
        user.phone = payload.phone
    if "avatar_url" in supplied:
        user.avatar_url = str(payload.avatar_url) if payload.avatar_url else None
    user.row_version += 1
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, payload: PasswordChange) -> None:
    if user.auth_source != "local" or not user.password_hash:
        raise AppError(409, "AUTH_PASSWORD_MANAGED_EXTERNALLY", "该账号密码由统一身份认证平台管理")
    if not verify_password(payload.old_password, user.password_hash):
        raise AppError(
            400,
            "AUTH_OLD_PASSWORD_INVALID",
            "原密码不正确",
            field_errors={"oldPassword": "原密码不正确"},
        )
    if verify_password(payload.new_password, user.password_hash):
        raise AppError(
            400,
            "AUTH_PASSWORD_UNCHANGED",
            "新密码不能与原密码相同",
            field_errors={"newPassword": "请使用不同的新密码"},
        )
    user.password_hash = hash_password(payload.new_password)
    user.row_version += 1
    now = utcnow()
    sessions = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    ).all()
    for auth_session in sessions:
        auth_session.revoked_at = now
        auth_session.revoked_reason = "password_changed"
    db.commit()
