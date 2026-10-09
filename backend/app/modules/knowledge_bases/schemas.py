import re
import uuid
from typing import Literal

from pydantic import Field, model_validator

from app.core.schemas import ApiModel


class AclEntryInput(ApiModel):
    is_public: bool = False
    role_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    can_search: bool = True
    can_preview: bool = True
    can_download: bool = False
    can_manage: bool = False

    @model_validator(mode="after")
    def one_subject(self):
        count = int(self.is_public) + sum(
            value is not None for value in (self.role_id, self.department_id, self.user_id)
        )
        if count != 1:
            raise ValueError("每条 ACL 必须且只能指定一种主体")
        return self


class RetrievalConfig(ApiModel):
    chunk_size: int = Field(default=800, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0, le=1000)
    top_k: int = Field(default=5, ge=1, le=50)
    similarity_threshold: float = Field(default=0.55, ge=0, le=1)
    rerank_enabled: bool = True
    hybrid_search_enabled: bool = True
    prompt_instruction: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def overlap_smaller_than_chunk(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunkOverlap 必须小于 chunkSize")
        return self


class KnowledgeBaseCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    department_id: uuid.UUID | None = None
    business_category: str | None = Field(default=None, max_length=100)
    selectable: bool = True
    owner_ids: list[uuid.UUID] = Field(default_factory=list)
    acl: list[AclEntryInput] = Field(default_factory=list)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)

    @model_validator(mode="after")
    def normalize_code(self):
        self.code = self.code.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]+", self.code):
            raise ValueError("code 只能使用大写字母、数字、下划线和短横线")
        return self


class KnowledgeBaseUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    department_id: uuid.UUID | None = None
    business_category: str | None = Field(default=None, max_length=100)
    selectable: bool | None = None
    owner_ids: list[uuid.UUID] | None = None
    retrieval: RetrievalConfig | None = None
    version: int = Field(ge=1)


class KnowledgeBaseStatus(ApiModel):
    status: Literal["enabled", "disabled"]
    reason: str = Field(min_length=2, max_length=500)


class KnowledgeBaseDelete(ApiModel):
    confirm_name: str


class AclReplace(ApiModel):
    entries: list[AclEntryInput]
    version: int = Field(ge=1)


class RebuildRequest(ApiModel):
    reason: str = Field(min_length=2, max_length=500)


class RetrievalTest(ApiModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=50)
    threshold: float | None = Field(default=None, ge=0, le=1)
    rerank: bool | None = None
    hybrid: bool | None = None
