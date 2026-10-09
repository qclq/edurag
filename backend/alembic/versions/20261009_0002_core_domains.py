"""Create knowledge, document, task, conversation, and operations tables.

Revision ID: 20261009_0002
Revises: 20261008_0001
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_0002"
down_revision = "20261008_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("department_id", sa.Uuid(), sa.ForeignKey("departments.id")),
        sa.Column("business_category", sa.String(100)),
        sa.Column("selectable", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("similarity_threshold", sa.Float(), nullable=False),
        sa.Column("rerank_enabled", sa.Boolean(), nullable=False),
        sa.Column("hybrid_search_enabled", sa.Boolean(), nullable=False),
        sa.Column("prompt_instruction", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("department_id", "name", name="uq_knowledge_bases_department_name"),
    )
    for columns in (("code",), ("department_id",), ("status",), ("created_by",), ("deleted_at",)):
        op.create_index(f"ix_knowledge_bases_{columns[0]}", "knowledge_bases", list(columns))
    op.create_table(
        "knowledge_base_owners",
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "knowledge_base_acl_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id")),
        sa.Column("department_id", sa.Uuid(), sa.ForeignKey("departments.id")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("can_search", sa.Boolean(), nullable=False),
        sa.Column("can_preview", sa.Boolean(), nullable=False),
        sa.Column("can_download", sa.Boolean(), nullable=False),
        sa.Column("can_manage", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "(CASE WHEN is_public THEN 1 ELSE 0 END + CASE WHEN role_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN department_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN user_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_knowledge_base_acl_entries_one_subject",
        ),
    )
    op.create_index("ix_knowledge_base_acl_entries_knowledge_base_id", "knowledge_base_acl_entries", ["knowledge_base_id"])

    op.create_table(
        "stored_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("storage_key", sa.String(1000), nullable=False, unique=True),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("scan_status", sa.String(30), nullable=False),
        sa.Column("scan_detail", sa.Text()),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_stored_files_sha256", "stored_files", ["sha256"])
    op.create_table(
        "pending_uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_file_id", sa.String(200), nullable=False),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pending_uploads_uploaded_by", "pending_uploads", ["uploaded_by"])
    op.create_index("ix_pending_uploads_expires_at", "pending_uploads", ["expires_at"])
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("display_name", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("publish_department_id", sa.Uuid(), sa.ForeignKey("departments.id")),
        sa.Column("document_no", sa.String(100)),
        sa.Column("published_at", sa.Date()),
        sa.Column("effective_at", sa.Date()),
        sa.Column("expires_at", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    for column in ("knowledge_base_id", "category", "document_no", "expires_at", "status", "deleted_at"):
        op.create_index(f"ix_documents_{column}", "documents", [column])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_files.id"), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("process_status", sa.String(30), nullable=False),
        sa.Column("process_stage", sa.String(50)),
        sa.Column("ocr_enabled", sa.Boolean(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(100)),
        sa.Column("failure_message", sa.Text()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "version_no", name="uq_document_versions_document_version"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_process_status", "document_versions", ["process_status"])
    op.create_table(
        "document_acl_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id")),
        sa.Column("department_id", sa.Uuid(), sa.ForeignKey("departments.id")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("can_search", sa.Boolean(), nullable=False),
        sa.Column("can_preview", sa.Boolean(), nullable=False),
        sa.Column("can_download", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_document_acl_entries_document_id", "document_acl_entries", ["document_id"])
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Uuid(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("section", sa.String(500)),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("char_start", sa.Integer()),
        sa.Column("char_end", sa.Integer()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("search_tokens", sa.Text()),
        sa.Column("vector_id", sa.String(200), unique=True),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("embedding_dimension", sa.Integer()),
        sa.Column("vector_status", sa.String(30), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_version_id", "chunk_index", name="uq_document_chunks_version_index"),
    )
    for column in ("document_version_id", "section", "page_start"):
        op.create_index(f"ix_document_chunks_{column}", "document_chunks", [column])

    op.create_table(
        "processing_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_no", sa.String(50), nullable=False, unique=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id")),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id")),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(50)),
        sa.Column("progress_percent", sa.Integer()),
        sa.Column("progress_text", sa.String(500)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("created_by", "idempotency_key", name="uq_processing_tasks_creator_idempotency"),
    )
    for column in ("task_no", "type", "knowledge_base_id", "document_id", "status", "created_by", "created_at"):
        op.create_index(f"ix_processing_tasks_{column}", "processing_tasks", [column])
    op.create_table(
        "task_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("processing_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_type", sa.String(50), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_task_items_task_id", "task_items", ["task_id"])
    op.create_index("ix_task_items_status", "task_items", ["status"])
    op.create_table(
        "task_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("processing_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("stage", sa.String(50)),
        sa.Column("payload", sa.JSON()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_events_task_sequence"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_table(
        "vector_index_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_name", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("operation", "chunk_id", "collection_name", name="uq_vector_outbox_operation_chunk_collection"),
    )
    for column in ("operation", "chunk_id", "status"):
        op.create_index(f"ix_vector_index_outbox_{column}", "vector_index_outbox", [column])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("is_favorite", sa.Boolean(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    for column in ("user_id", "status", "is_favorite", "last_message_at", "deleted_at"):
        op.create_index(f"ix_conversations_{column}", "conversations", [column])
    op.create_table(
        "conversation_knowledge_bases",
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_message_id", sa.Uuid(), sa.ForeignKey("messages.id")),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("generation_version", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retrieval_ms", sa.Integer()),
        sa.Column("first_token_ms", sa.Integer()),
        sa.Column("total_ms", sa.Integer()),
        sa.Column("model_name", sa.String(200)),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_messages_conversation_idempotency"),
    )
    for column in ("conversation_id", "status", "created_at"):
        op.create_index(f"ix_messages_{column}", "messages", [column])
    op.create_table(
        "message_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("message_id", "sequence", name="uq_message_events_message_sequence"),
    )
    op.create_index("ix_message_events_message_id", "message_events", ["message_id"])
    op.create_table(
        "message_citations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("citation_index", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("document_chunks.id"), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("context_before", sa.Text()),
        sa.Column("context_after", sa.Text()),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("section", sa.String(500)),
        sa.Column("similarity_score", sa.Float()),
        sa.Column("rerank_score", sa.Float()),
        sa.UniqueConstraint("message_id", "citation_index", name="uq_message_citations_message_index"),
    )
    op.create_index("ix_message_citations_message_id", "message_citations", ["message_id"])
    op.create_index("ix_message_citations_document_id", "message_citations", ["document_id"])
    op.create_table(
        "message_ratings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("message_id", "user_id", name="uq_message_ratings_message_user"),
    )
    op.create_index("ix_message_ratings_message_id", "message_ratings", ["message_id"])
    op.create_index("ix_message_ratings_user_id", "message_ratings", ["user_id"])

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id")),
        sa.Column("citation_id", sa.Uuid(), sa.ForeignKey("message_citations.id")),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id")),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id")),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("resolution", sa.String(100)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("notify_user", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("message_id", "conversation_id", "knowledge_base_id", "created_by", "type", "status", "assignee_id", "created_at"):
        op.create_index(f"ix_feedbacks_{column}", "feedbacks", [column])
    op.create_table(
        "feedback_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("feedback_id", sa.Uuid(), sa.ForeignKey("feedbacks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("from_status", sa.String(30)),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("related_document_id", sa.Uuid(), sa.ForeignKey("documents.id")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedback_events_feedback_id", "feedback_events", ["feedback_id"])

    op.create_table(
        "recommended_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), sa.ForeignKey("knowledge_bases.id")),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
    )
    op.create_index("ix_recommended_questions_category", "recommended_questions", ["category"])
    op.create_index("ix_recommended_questions_enabled", "recommended_questions", ["enabled"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(50)),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("batch_key", sa.String(200)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("user_id", "batch_key", "read_at", "created_at"):
        op.create_index(f"ix_notifications_{column}", "notifications", [column])
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("module", sa.String(50), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50)),
        sa.Column("target_id", sa.String(100)),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("request_id", sa.String(200)),
        sa.Column("before_json", sa.JSON()),
        sa.Column("after_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("actor_id", "module", "action", "target_id", "request_id", "created_at"):
        op.create_index(f"ix_audit_logs_{column}", "audit_logs", [column])
    op.create_table(
        "user_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("rows_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_imports_created_by", "user_imports", ["created_by"])


def downgrade() -> None:
    for table in (
        "user_imports",
        "audit_logs",
        "system_settings",
        "notifications",
        "recommended_questions",
        "feedback_events",
        "feedbacks",
        "message_ratings",
        "message_citations",
        "message_events",
        "messages",
        "conversation_knowledge_bases",
        "conversations",
        "vector_index_outbox",
        "task_events",
        "task_items",
        "processing_tasks",
        "document_chunks",
        "document_tags",
        "tags",
        "document_acl_entries",
        "document_versions",
        "documents",
        "pending_uploads",
        "stored_files",
        "knowledge_base_acl_entries",
        "knowledge_base_owners",
        "knowledge_bases",
    ):
        op.drop_table(table)
