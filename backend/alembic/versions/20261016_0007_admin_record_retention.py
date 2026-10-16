"""add administrator QA retention summaries and persistent knowledge gaps

Revision ID: 20261016_0007
Revises: 20261016_0006
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20261016_0007"
down_revision = "20261016_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("source_conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(200)),
        sa.Column("total_ms", sa.Integer()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_message_id", name="uq_qa_records_source_message_id"),
    )
    for column in ("source_message_id", "source_conversation_id", "user_id", "status", "created_at"):
        op.create_index(f"ix_qa_records_{column}", "qa_records", [column])

    op.create_table(
        "qa_record_knowledge_bases",
        sa.Column("qa_record_id", sa.Uuid(), sa.ForeignKey("qa_records.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "qa_archive_summaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("answered_count", sa.Integer(), nullable=False),
        sa.Column("no_answer_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("helpful_count", sa.Integer(), nullable=False),
        sa.Column("unhelpful_count", sa.Integer(), nullable=False),
        sa.Column("response_ms_total", sa.Integer(), nullable=False),
        sa.Column("response_ms_samples", sa.Integer(), nullable=False),
        sa.Column("resolved_feedback_count", sa.Integer(), nullable=False),
        sa.Column("supplemented_gap_count", sa.Integer(), nullable=False),
        sa.Column("top_questions", sa.JSON(), nullable=False),
        sa.Column("feedback_types", sa.JSON(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("knowledge_base_id", "summary_date", name="uq_qa_archive_summary_kb_date"),
    )
    op.create_index("ix_qa_archive_summaries_knowledge_base_id", "qa_archive_summaries", ["knowledge_base_id"])
    op.create_index("ix_qa_archive_summaries_summary_date", "qa_archive_summaries", ["summary_date"])

    op.create_table(
        "knowledge_gaps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_fingerprint", sa.String(64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supplemented_at", sa.DateTime(timezone=True)),
        sa.Column("supplemented_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("supplement_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("knowledge_base_id", "question_fingerprint", name="uq_knowledge_gap_kb_question"),
    )
    for column in ("knowledge_base_id", "status", "last_occurred_at", "supplemented_at"):
        op.create_index(f"ix_knowledge_gaps_{column}", "knowledge_gaps", [column])

    op.create_table(
        "knowledge_gap_occurrences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("gap_id", sa.Uuid(), sa.ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("gap_id", "source_message_id", name="uq_knowledge_gap_occurrence_source"),
    )
    op.create_index("ix_knowledge_gap_occurrences_gap_id", "knowledge_gap_occurrences", ["gap_id"])
    op.create_index("ix_knowledge_gap_occurrences_source_message_id", "knowledge_gap_occurrences", ["source_message_id"])


def downgrade() -> None:
    op.drop_table("knowledge_gap_occurrences")
    op.drop_table("knowledge_gaps")
    op.drop_table("qa_archive_summaries")
    op.drop_table("qa_record_knowledge_bases")
    op.drop_table("qa_records")
