"""add forced password change state

Revision ID: 20261015_0003
Revises: 20261009_0002
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20261015_0003"
down_revision = "20261009_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
