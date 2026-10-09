import uuid
from datetime import date
from typing import Literal

from pydantic import Field

from app.core.schemas import ApiModel


class DocumentMetadata(ApiModel):
    knowledge_base_id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    publish_department_id: uuid.UUID | None = None
    document_no: str | None = Field(default=None, max_length=100)
    published_at: date | None = None
    effective_at: date | None = None
    expires_at: date | None = None
    notes: str | None = Field(default=None, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class IngestionItem(ApiModel):
    upload_id: uuid.UUID
    metadata: DocumentMetadata


class ProcessingConfig(ApiModel):
    ocr_enabled: bool = False
    chunk_size: int = Field(default=800, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0, le=1000)


class IngestionCreate(ApiModel):
    items: list[IngestionItem] = Field(min_length=1, max_length=20)
    processing_config: ProcessingConfig = Field(default_factory=ProcessingConfig)
    save_as_draft: bool = False


class DocumentUpdate(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    publish_department_id: uuid.UUID | None = None
    document_no: str | None = Field(default=None, max_length=100)
    published_at: date | None = None
    effective_at: date | None = None
    expires_at: date | None = None
    notes: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=30)
    version: int = Field(ge=1)


class NewVersion(ApiModel):
    upload_id: uuid.UUID
    switch_policy: Literal["after_ready", "immediate"] = "after_ready"


class ReprocessRequest(ProcessingConfig):
    reason: str = Field(min_length=2, max_length=500)


class DocumentStatus(ApiModel):
    status: Literal["enabled", "disabled"]
    reason: str = Field(min_length=2, max_length=500)


class DocumentDelete(ApiModel):
    confirm_name: str


class ChunkUpdate(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=2, max_length=500)
    version: int = Field(ge=1)


class DocumentRetrievalTest(ApiModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0, ge=0, le=1)
