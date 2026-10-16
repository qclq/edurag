import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import hash_password, utcnow, verify_password
from app.integrations.storage import get_storage
from app.modules.auth.models import AuthSession, LoginEvent
from app.modules.users.models import User
from app.modules.users.schemas import PasswordChange, ProfileUpdate


AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_FILE_PATTERN = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
AVATAR_MIME_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def _detect_avatar(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", AVATAR_MIME_TYPES["jpg"]
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", AVATAR_MIME_TYPES["png"]
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp", AVATAR_MIME_TYPES["webp"]
    raise AppError(415, "AVATAR_TYPE_UNSUPPORTED", "头像仅支持 JPG、PNG 或 WebP 图片")


def avatar_storage_details(user_id: uuid.UUID, file_name: str) -> tuple[str, str]:
    match = AVATAR_FILE_PATTERN.fullmatch(file_name)
    if not match:
        raise AppError(404, "AVATAR_NOT_FOUND", "头像不存在")
    extension = match.group(1)
    return f"avatars/{user_id}/{file_name}", AVATAR_MIME_TYPES[extension]


def _stored_avatar_key(avatar_url: str | None, user_id: uuid.UUID, settings: Settings) -> str | None:
    prefix = f"{settings.api_prefix}/me/avatars/{user_id}/"
    if not avatar_url or not avatar_url.startswith(prefix):
        return None
    file_name = avatar_url.removeprefix(prefix)
    try:
        key, _ = avatar_storage_details(user_id, file_name)
        return key
    except AppError:
        return None


def save_avatar(db: Session, user: User, content: bytes, settings: Settings) -> User:
    if user.user_type == "external":
        raise AppError(403, "EXTERNAL_PROFILE_UPDATE_FORBIDDEN", "外部人员不能修改共享访客头像")
    if not content:
        raise AppError(400, "AVATAR_EMPTY", "请选择头像图片")
    if len(content) > AVATAR_MAX_BYTES:
        raise AppError(413, "AVATAR_TOO_LARGE", "头像图片不能超过 2 MB")
    extension, mime_type = _detect_avatar(content)
    file_name = f"{uuid.uuid4().hex}.{extension}"
    storage_key, _ = avatar_storage_details(user.id, file_name)
    storage = get_storage(settings)
    storage.put(storage_key, content, mime_type)
    old_storage_key = _stored_avatar_key(user.avatar_url, user.id, settings)
    user.avatar_url = f"{settings.api_prefix}/me/avatars/{user.id}/{file_name}"
    user.row_version += 1
    db.commit()
    db.refresh(user)
    if old_storage_key:
        try:
            storage.delete(old_storage_key)
        except OSError:
            pass
    return user


def remove_avatar(db: Session, user: User, settings: Settings) -> User:
    if user.user_type == "external":
        raise AppError(403, "EXTERNAL_PROFILE_UPDATE_FORBIDDEN", "外部人员不能修改共享访客头像")
    old_storage_key = _stored_avatar_key(user.avatar_url, user.id, settings)
    user.avatar_url = None
    user.row_version += 1
    db.commit()
    db.refresh(user)
    if old_storage_key:
        try:
            get_storage(settings).delete(old_storage_key)
        except OSError:
            pass
    return user


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
        "mustChangePassword": user.must_change_password,
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
    if user.user_type == "external" and "avatar_url" in supplied:
        raise AppError(403, "EXTERNAL_PROFILE_UPDATE_FORBIDDEN", "外部人员不能修改共享访客头像")
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


def change_password(
    db: Session,
    user: User,
    payload: PasswordChange,
    *,
    current_session: AuthSession,
) -> None:
    if user.auth_source != "local" or not user.password_hash:
        raise AppError(409, "AUTH_PASSWORD_MANAGED_EXTERNALLY", "该账号密码由统一身份认证平台管理")
    if not user.must_change_password and (
        not payload.old_password or not verify_password(payload.old_password, user.password_hash)
    ):
        raise AppError(
            400,
            "AUTH_OLD_PASSWORD_INVALID",
            "请输入正确的当前密码",
            field_errors={"oldPassword": "请输入正确的当前密码"},
        )
    if verify_password(payload.new_password, user.password_hash):
        raise AppError(
            400,
            "AUTH_PASSWORD_UNCHANGED",
            "新密码不能与原密码相同",
            field_errors={"newPassword": "请使用不同的新密码"},
        )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.row_version += 1
    now = utcnow()
    sessions = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    ).all()
    for auth_session in sessions:
        if auth_session.id == current_session.id:
            continue
        auth_session.revoked_at = now
        auth_session.revoked_reason = "password_changed"
    db.commit()
