from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.core.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True)


class LoginRequest(ApiModel):
    account: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    captcha_id: str | None = None
    captcha_code: str | None = Field(default=None, min_length=1, max_length=10)
    # Kept for backwards compatibility with older clients. All successful
    # logins now use the configured monthly persistent-session policy.
    remember: bool = True


class SsoCallbackQuery(ApiModel):
    state: str
    code: str | None = None
    ticket: str | None = None
    error: str | None = None
    error_description: str | None = None


SsoChannel = Literal["student", "staff"]
