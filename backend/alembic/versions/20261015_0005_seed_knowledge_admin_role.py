"""seed the administrator role

Revision ID: 20261015_0005
Revises: 20261015_0004
Create Date: 2026-10-15
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "20261015_0005"
down_revision = "20261015_0004"
branch_labels = None
depends_on = None


roles = sa.table(
    "roles",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("description", sa.String()),
    sa.column("is_system", sa.Boolean()),
)
permissions = sa.table(
    "permissions",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
)
role_permissions = sa.table(
    "role_permissions",
    sa.column("role_id", sa.Uuid()),
    sa.column("permission_id", sa.Uuid()),
)


def upgrade() -> None:
    connection = op.get_bind()
    role_id = connection.scalar(sa.select(roles.c.id).where(roles.c.code == "knowledge_admin"))
    if role_id is None:
        role_id = uuid.uuid4()
        connection.execute(
            roles.insert().values(
                id=role_id,
                code="knowledge_admin",
                name="管理员",
                description="可使用全部管理能力，但不能管理用户账户信息",
                is_system=True,
            )
        )
    else:
        connection.execute(
            roles.update()
            .where(roles.c.id == role_id)
            .values(name="管理员", description="可使用全部管理能力，但不能管理用户账户信息")
        )
    user_account_management = sa.or_(
        permissions.c.code.like("user.%"),
        permissions.c.code == "role.assign",
    )
    excluded_permission_ids = sa.select(permissions.c.id).where(user_account_management)
    connection.execute(
        role_permissions.delete().where(
            role_permissions.c.role_id == role_id,
            role_permissions.c.permission_id.in_(excluded_permission_ids),
        )
    )
    permission_ids = connection.scalars(
        sa.select(permissions.c.id).where(sa.not_(user_account_management))
    ).all()
    existing = set(
        connection.scalars(
            sa.select(role_permissions.c.permission_id).where(role_permissions.c.role_id == role_id)
        ).all()
    )
    if missing := [{"role_id": role_id, "permission_id": item} for item in permission_ids if item not in existing]:
        connection.execute(role_permissions.insert(), missing)


def downgrade() -> None:
    # Keep the system role because user assignments may depend on it.
    pass
