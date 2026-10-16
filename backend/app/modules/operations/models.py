import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, String, Table, Text, Uuid, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


qa_record_knowledge_bases = Table(
    "qa_record_knowledge_bases",
    Base.metadata,
    Column("qa_record_id", Uuid(as_uuid=True), ForeignKey("qa_records.id", ondelete="CASCADE"), primary_key=True),
    Column("knowledge_base_id", Uuid(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
)


class QaRecord(Base):
    """Short-lived administrator-facing copy of a completed Q&A interaction."""

    __tablename__ = "qa_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_message_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False, index=True)
    source_conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    citation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(200))
    total_ms: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    knowledge_bases = relationship("KnowledgeBase", secondary=qa_record_knowledge_bases, lazy="selectin")


class QaArchiveMarker(Base):
    """Compact tombstone that prevents historical messages from being backfilled twice."""

    __tablename__ = "qa_archive_markers"

    source_message_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QaArchiveSummary(Base):
    """Long-lived, non-identifying daily aggregate produced before raw QA records are removed."""

    __tablename__ = "qa_archive_summaries"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "summary_date", name="uq_qa_archive_summary_kb_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    summary_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    question_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    no_answer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unhelpful_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_ms_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_ms_samples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolved_feedback_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    supplemented_gap_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    top_questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    feedback_types: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class KnowledgeGap(Base):
    __tablename__ = "knowledge_gaps"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "question_fingerprint", name="uq_knowledge_gap_kb_question"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False, index=True)
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    supplemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    supplemented_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    supplement_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    occurrences: Mapped[list["KnowledgeGapOccurrence"]] = relationship(back_populates="gap", cascade="all, delete-orphan")


class KnowledgeGapOccurrence(Base):
    __tablename__ = "knowledge_gap_occurrences"
    __table_args__ = (UniqueConstraint("gap_id", "source_message_id", name="uq_knowledge_gap_occurrence_source"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gap_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False, index=True)
    source_message_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    gap: Mapped[KnowledgeGap] = relationship(back_populates="occurrences")


class RecommendedQuestion(Base):
    __tablename__ = "recommended_questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_bases.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    batch_key: Mapped[str | None] = mapped_column(String(200), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    module: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(100), index=True)
    result: Mapped[str] = mapped_column(String(30), default="success", nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(200), index=True)
    before_json: Mapped[Any | None] = mapped_column(JSON)
    after_json: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class UserImport(Base):
    __tablename__ = "user_imports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="validated", nullable=False)
    rows_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
