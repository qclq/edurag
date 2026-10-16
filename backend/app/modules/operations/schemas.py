import uuid
from typing import Any, Literal

from pydantic import Field

from app.core.schemas import ApiModel


class ReportCreate(ApiModel):
    type: str = Field(min_length=1, max_length=50)
    range: Literal["today", "7d", "30d"] = "7d"
    filters: dict[str, Any] = Field(default_factory=dict)


class SettingsUpdate(ApiModel):
    values: dict[str, Any]
    version: int = Field(ge=1)


class UserCreate(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    account: str | None = Field(default=None, min_length=2, max_length=100)
    student_or_staff_no: str | None = Field(default=None, max_length=50)
    user_type: Literal["student", "staff", "external"] = "student"
    department_id: uuid.UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    role_codes: list[str] = Field(default_factory=lambda: ["normal_user"])
    status: Literal["active", "disabled"] = "active"


class AdminUserUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    student_or_staff_no: str | None = Field(default=None, max_length=50)
    user_type: Literal["student", "staff", "external"] | None = None
    department_id: uuid.UUID | None = None
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=30)
    version: int = Field(ge=1)


class UserStatusUpdate(ApiModel):
    status: Literal["active", "disabled"]
    reason: str = Field(min_length=2, max_length=500)


class UserRolesUpdate(ApiModel):
    role_codes: list[str] = Field(min_length=1)
    reason: str = Field(min_length=2, max_length=500)
    version: int = Field(ge=1)


class PasswordReset(ApiModel):
    delivery_method: Literal["email", "sms", "manual"]


class ImportDecision(ApiModel):
    row: int = Field(ge=1)
    action: Literal["exclude", "update", "edit"]
    values: dict[str, Any] | None = None


class ImportCommit(ApiModel):
    decisions: list[ImportDecision] = Field(default_factory=list)


class UserExportCreate(ApiModel):
    filters: dict[str, Any] = Field(default_factory=dict)
