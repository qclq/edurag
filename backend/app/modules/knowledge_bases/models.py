import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Table, Text, Uuid, Column, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


knowledge_base_owners = Table(
    "knowledge_base_owners",
    Base.metadata,
    Column("knowledge_base_id", Uuid(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("code", name="uq_knowledge_bases_code"),
        UniqueConstraint("department_id", "name", name="uq_knowledge_bases_department_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"), index=True)
    business_category: Mapped[str | None] = mapped_column(String(100))
    selectable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="enabled", nullable=False, index=True)
    chunk_size: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Float, default=0.55, nullable=False)
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hybrid_search_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    prompt_instruction: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    owners = relationship("User", secondary=knowledge_base_owners, lazy="selectin")
    acl_entries: Mapped[list["KnowledgeBaseAclEntry"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan", lazy="selectin"
    )


class KnowledgeBaseAclEntry(Base):
    __tablename__ = "knowledge_base_acl_entries"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN is_public THEN 1 ELSE 0 END + CASE WHEN role_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN department_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN user_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="one_subject",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id"))
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    can_search: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_preview: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="acl_entries")
