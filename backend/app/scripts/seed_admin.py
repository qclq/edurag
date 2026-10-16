import argparse
import getpass

from sqlalchemy import select

from app.core.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, password_length_is_valid
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.modules.users.models import Permission, Role, User


PERMISSION_CODES = (
    "dashboard.read",
    "qa.read",
    "qa.content.read",
    "qa.analytics",
    "kb.read",
    "kb.create",
    "kb.update",
    "kb.status",
    "kb.delete",
    "kb.rebuild",
    "kb.test",
    "kb.permission.read",
    "kb.permission.update",
    "document.read",
    "document.create",
    "document.update",
    "document.status",
    "document.delete",
    "document.download",
    "document.reprocess",
    "chunk.update",
    "task.read",
    "task.retry",
    "task.cancel",
    "feedback.read",
    "feedback.manage",
    "user.read",
    "user.create",
    "user.update",
    "user.status",
    "user.import",
    "user.export",
    "user.password.reset",
    "role.assign",
    "system.read",
    "system.update",
    "audit.read",
)

USER_ACCOUNT_MANAGEMENT_PERMISSION_CODES = frozenset(
    code for code in PERMISSION_CODES if code.startswith("user.")
) | {"role.assign"}

KNOWLEDGE_ADMIN_PERMISSION_CODES = tuple(
    code for code in PERMISSION_CODES if code not in USER_ACCOUNT_MANAGEMENT_PERMISSION_CODES
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the initial EduRAG super administrator")
    parser.add_argument("--account", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass.getpass(
        f"Initial password ({PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters): "
    )
    if not password_length_is_valid(password):
        raise SystemExit(
            f"Password must contain {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters"
        )
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.account == args.account.lower())):
            raise SystemExit("Account already exists")
        permissions = []
        for code in PERMISSION_CODES:
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code, name=code, module=code.split(".", 1)[0])
                db.add(permission)
            permissions.append(permission)
        role = db.scalar(select(Role).where(Role.code == "super_admin"))
        if role is None:
            role = Role(code="super_admin", name="超级管理员", is_system=True)
            db.add(role)
        role.permissions = permissions
        knowledge_admin = db.scalar(select(Role).where(Role.code == "knowledge_admin"))
        if knowledge_admin is None:
            knowledge_admin = Role(
                code="knowledge_admin",
                name="管理员",
                description="可使用全部管理能力，但不能管理用户账户信息",
                is_system=True,
            )
            db.add(knowledge_admin)
        else:
            knowledge_admin.name = "管理员"
            knowledge_admin.description = "可使用全部管理能力，但不能管理用户账户信息"
        knowledge_admin.permissions = [
            permission
            for permission in permissions
            if permission.code in KNOWLEDGE_ADMIN_PERMISSION_CODES
        ]
        db.add(
            User(
                account=args.account.lower(),
                name=args.name,
                password_hash=hash_password(password),
                user_type="staff",
                status="active",
                auth_source="local",
                roles=[role],
            )
        )
    print(f"Created super administrator: {args.account.lower()}")


if __name__ == "__main__":
    main()
