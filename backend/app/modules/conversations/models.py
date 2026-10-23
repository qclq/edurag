import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Table, Text, Uuid, Column, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


conversation_knowledge_bases = Table(
    "conversation_knowledge_bases",
    Base.metadata,
    Column("conversation_id", Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
    Column("knowledge_base_id", Uuid(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), default="新对话", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False, index=True)
    is_favorite: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    knowledge_bases = relationship("KnowledgeBase", secondary=conversation_knowledge_bases, lazy="selectin")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at", lazy="selectin"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "idempotency_key", name="uq_messages_conversation_idempotency"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="submitted", nullable=False, index=True)
    generation_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    model_name: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    events: Mapped[list["MessageEvent"]] = relationship(back_populates="message", cascade="all, delete-orphan", order_by="MessageEvent.sequence")
    citations: Mapped[list["MessageCitation"]] = relationship(back_populates="message", cascade="all, delete-orphan", order_by="MessageCitation.citation_index")
    ratings: Mapped[list["MessageRating"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class MessageEvent(Base):
    __tablename__ = "message_events"
    __table_args__ = (UniqueConstraint("message_id", "sequence", name="uq_message_events_message_sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    message: Mapped[Message] = relationship(back_populates="events")


class MessageCitation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (UniqueConstraint("message_id", "citation_index", name="uq_message_citations_message_index"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    citation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_chunks.id"))
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    context_before: Mapped[str | None] = mapped_column(Text)
    context_after: Mapped[str | None] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(500))
    similarity_score: Mapped[float | None] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)

    message: Mapped[Message] = relationship(back_populates="citations")
    document = relationship("Document", lazy="joined")
    document_version = relationship("DocumentVersion", lazy="joined")


class MessageRating(Base):
    __tablename__ = "message_ratings"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_message_ratings_message_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
