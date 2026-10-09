from pydantic import Field

from app.core.schemas import ApiModel


class RetryTask(ApiModel):
    from_stage: str | None = Field(default=None, max_length=50)
