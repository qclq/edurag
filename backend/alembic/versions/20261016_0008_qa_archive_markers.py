"""add idempotent QA archive markers

Revision ID: 20261016_0008
Revises: 20261016_0007
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20261016_0008"
down_revision = "20261016_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_archive_markers",
        sa.Column("source_message_id", sa.Uuid(), primary_key=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("qa_archive_markers")
