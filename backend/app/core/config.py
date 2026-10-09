import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
    )

    app_name: str = "智慧校园知识库问答系统"
    app_description: str = "Campus AI Knowledge Base"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:5173"
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    database_url: str = "postgresql+pg8000://edurag:edurag@localhost:5432/edurag"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-only-change-me-before-production"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "edurag"
    jwt_audience: str = "edurag-web"
    access_token_minutes: int = 15
    refresh_session_hours: int = 24
    remember_session_days: int = 30
    refresh_cookie_name: str = "edurag_refresh"
    cookie_secure: bool = False

    captcha_enabled: bool = True
    captcha_length: int = 4
    captcha_ttl_seconds: int = 300
    captcha_max_attempts: int = 5
    login_max_failures: int = 5
    login_failure_window_minutes: int = 15
    account_lock_minutes: int = 15

    question_max_length: int = 500
    upload_max_file_mb: int = 50
    upload_max_files: int = 20
    upload_extensions: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["pdf", "docx", "txt", "md"])
    upload_ttl_hours: int = 24

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/files"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "edurag-documents"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    task_dispatch_mode: Literal["celery", "inline"] = "celery"
    task_event_poll_seconds: float = 1.0

    milvus_enabled: bool = True
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str | None = None
    milvus_database: str = "default"
    milvus_collection_prefix: str = "edurag_chunks"
    milvus_collection_alias: str = "edurag_chunks_current"
    milvus_timeout_seconds: float = 10.0
    embedding_provider: Literal["qwen", "local"] = "qwen"
    qwen_embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str | None = None
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    embedding_batch_size: int = 10
    embedding_timeout_seconds: float = 30.0

    llm_provider: Literal["deepseek", "extractive"] = "deepseek"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-flash"
    deepseek_thinking: Literal["enabled", "disabled"] = "disabled"
    deepseek_reasoning_effort: Literal["low", "medium", "high"] = "medium"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    llm_timeout_seconds: float = 60.0

    sso_provider: Literal["disabled", "oidc", "cas"] = "disabled"
    sso_auto_provision: bool = False
    sso_client_id: str | None = None
    sso_client_secret: str | None = None
    sso_authorize_url: AnyHttpUrl | None = None
    sso_token_url: AnyHttpUrl | None = None
    sso_userinfo_url: AnyHttpUrl | None = None
    sso_cas_base_url: AnyHttpUrl | None = None
    sso_scopes: str = "openid profile email"

    @field_validator("allowed_origins", "upload_extensions", mode="before")
    @classmethod
    def split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key == "dev-only-change-me-before-production" or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be a unique value of at least 32 characters in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be enabled in production")
        if self.sso_provider == "oidc" and not all(
            (self.sso_client_id, self.sso_authorize_url, self.sso_token_url, self.sso_userinfo_url)
        ):
            raise ValueError("OIDC SSO requires client ID, authorize, token, and userinfo URLs")
        if self.sso_provider == "cas" and not self.sso_cas_base_url:
            raise ValueError("CAS SSO requires SSO_CAS_BASE_URL")
        if self.storage_backend == "s3" and not all(
            (self.s3_endpoint_url, self.s3_access_key, self.s3_secret_key)
        ):
            raise ValueError("S3 storage requires endpoint and access credentials")
        if self.embedding_dimension != 1024:
            raise ValueError("EMBEDDING_DIMENSION must stay 1024 for the current Milvus collection")
        if not 1 <= self.embedding_batch_size <= 10:
            raise ValueError("EMBEDDING_BATCH_SIZE must be between 1 and 10 for text-embedding-v4")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
