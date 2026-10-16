import base64
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.scripts.seed_admin import KNOWLEDGE_ADMIN_PERMISSION_CODES, PERMISSION_CODES
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.users.models import Department, Permission, Role, User


@pytest.fixture
def test_context(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        secret_key="test-secret-key-that-is-long-enough-for-tests",
        captcha_enabled=True,
        cookie_secure=False,
        login_max_failures=3,
        account_lock_minutes=1,
        sso_provider="disabled",
        storage_local_path=str(tmp_path / "files"),
        task_dispatch_mode="inline",
        embedding_provider="local",
        milvus_enabled=False,
        llm_provider="extractive",
    )

    def override_db():
        with TestingSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings

    with TestingSession.begin() as db:
        department = Department(name="教务处", code="ACADEMIC")
        permissions = [
            Permission(code=code, name=code, module=code.split(".", 1)[0])
            for code in PERMISSION_CODES
        ]
        db.add_all(permissions)
        normal = Role(code="normal_user", name="普通用户", is_system=True)
        admin = Role(
            code="knowledge_admin",
            name="管理员",
            is_system=True,
            permissions=[
                permission
                for permission in permissions
                if permission.code in KNOWLEDGE_ADMIN_PERMISSION_CODES
            ],
        )
        user = User(
            account="2026000001",
            password_hash=hash_password("OldPassword!123"),
            name="李明",
            student_or_staff_no="2026000001",
            user_type="staff",
            subtitle="教务处 · 管理员",
            department=department,
            email="liming@example.edu.cn",
            phone="13800138000",
            status="active",
            auth_source="local",
            roles=[normal, admin],
        )
        db.add(user)

    with TestClient(app) as client:
        yield client, TestingSession, settings

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def captcha_value(client: TestClient) -> tuple[str, str]:
    response = client.get("/api/v1/auth/captcha")
    assert response.status_code == 200
    data = response.json()["data"]
    encoded = data["imageDataUrl"].split(",", 1)[1]
    svg = base64.b64decode(encoded).decode()
    code = "".join(re.findall(r"<text[^>]*>([^<])</text>", svg))
    return data["captchaId"], code


def login(client: TestClient, password: str = "OldPassword!123", account: str = "2026000001"):
    captcha_id, captcha_code = captcha_value(client)
    return client.post(
        "/api/v1/auth/login",
        json={
            "account": account,
            "password": password,
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
            "remember": True,
        },
    )
