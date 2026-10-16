"""seed the required normal user role

Revision ID: 20261015_0004
Revises: 20261015_0003
Create Date: 2026-10-15
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "20261015_0004"
down_revision = "20261015_0003"
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


def upgrade() -> None:
    connection = op.get_bind()
    exists = connection.scalar(sa.select(roles.c.id).where(roles.c.code == "normal_user"))
    if exists is None:
        connection.execute(
            roles.insert().values(
                id=uuid.uuid4(),
                code="normal_user",
                name="普通用户",
                description="系统默认普通用户角色",
                is_system=True,
            )
        )


def downgrade() -> None:
    # Keep the role because user-role assignments may already depend on it.
    pass
