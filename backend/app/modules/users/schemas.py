import re

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.alias_generators import to_camel

from app.core.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True)


class ProfileUpdate(ApiModel):
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    avatar_url: AnyHttpUrl | None = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is not None and value != "" and not re.fullmatch(r"[+\d][\d\s()-]{5,28}\d", value):
            raise ValueError("手机号格式不正确")
        return value or None


class PasswordChange(ApiModel):
    old_password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
