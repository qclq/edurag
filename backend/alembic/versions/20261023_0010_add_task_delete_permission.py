"""add permission for deleting terminal task records

Revision ID: 20261023_0010
Revises: 20261023_0009
Create Date: 2026-10-23
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "20261023_0010"
down_revision = "20261023_0009"
branch_labels = None
depends_on = None


roles = sa.table(
    "roles",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
)
permissions = sa.table(
    "permissions",
    sa.column("id", sa.Uuid()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("module", sa.String()),
)
role_permissions = sa.table(
    "role_permissions",
    sa.column("role_id", sa.Uuid()),
    sa.column("permission_id", sa.Uuid()),
)


def upgrade() -> None:
    connection = op.get_bind()
    permission_id = connection.scalar(
        sa.select(permissions.c.id).where(permissions.c.code == "task.delete")
    )
    if permission_id is None:
        permission_id = uuid.uuid4()
        connection.execute(
            permissions.insert().values(
                id=permission_id,
                code="task.delete",
                name="删除任务记录",
                module="task",
            )
        )
    role_ids = connection.scalars(
        sa.select(roles.c.id).where(roles.c.code.in_(["super_admin", "knowledge_admin"]))
    ).all()
    for role_id in role_ids:
        exists = connection.scalar(
            sa.select(role_permissions.c.role_id).where(
                role_permissions.c.role_id == role_id,
                role_permissions.c.permission_id == permission_id,
            )
        )
        if exists is None:
            connection.execute(
                role_permissions.insert().values(
                    role_id=role_id,
                    permission_id=permission_id,
                )
            )


def downgrade() -> None:
    connection = op.get_bind()
    permission_id = connection.scalar(
        sa.select(permissions.c.id).where(permissions.c.code == "task.delete")
    )
    if permission_id is not None:
        connection.execute(
            role_permissions.delete().where(role_permissions.c.permission_id == permission_id)
        )
        connection.execute(permissions.delete().where(permissions.c.id == permission_id))
