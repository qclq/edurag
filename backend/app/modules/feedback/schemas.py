import uuid
from typing import Literal

from pydantic import Field

from app.core.schemas import ApiModel


class FeedbackResolve(ApiModel):
    resolution: Literal[
        "answer_correct",
        "missing_document",
        "document_outdated",
        "document_conflict",
        "irrelevant_retrieval",
        "generation_error",
        "insufficient_question",
        "other",
    ]
    note: str = Field(min_length=1, max_length=5000)
    related_document_ids: list[uuid.UUID] = Field(default_factory=list)
    notify_user: bool = True
