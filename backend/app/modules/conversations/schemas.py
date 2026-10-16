import uuid
from typing import Literal

from pydantic import Field, model_validator

from app.core.schemas import ApiModel


class ConversationCreate(ApiModel):
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    title: str | None = Field(default=None, max_length=300)


class ConversationUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    favorite: bool | None = None

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个待更新字段")
        return self


class MessageCreate(ApiModel):
    content: str = Field(min_length=1, max_length=500)
    knowledge_base_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=20)
    client_request_id: str = Field(min_length=8, max_length=200)


class RegenerationCreate(ApiModel):
    replace_current: bool = False


class RatingUpdate(ApiModel):
    value: Literal["helpful", "unhelpful", "none"]
    reason: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)


class CitationReportCreate(ApiModel):
    type: Literal["outdated", "incorrect", "other"]
    comment: str | None = Field(default=None, max_length=2000)
