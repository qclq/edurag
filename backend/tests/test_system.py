from app.core.config import Settings, get_settings
from app.main import app


def test_list_settings_accept_comma_separated_and_json_values():
    comma_separated = Settings(
        _env_file=None,
        allowed_origins="http://localhost:5173,http://localhost:8080",
        upload_extensions="pdf,md",
    )
    assert comma_separated.allowed_origins == ["http://localhost:5173", "http://localhost:8080"]
    assert comma_separated.upload_extensions == ["pdf", "md"]

    json_encoded = Settings(
        _env_file=None,
        allowed_origins='["http://localhost:8080","http://127.0.0.1:8080"]',
    )
    assert json_encoded.allowed_origins == ["http://localhost:8080", "http://127.0.0.1:8080"]


def test_bootstrap_contract(test_context):
    client, _, _ = test_context
    response = client.get("/api/v1/system/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["systemName"] == "智慧校园知识库问答系统"
    assert body["data"]["captchaPolicy"]["enabled"] is True
    assert body["data"]["passwordPolicy"] == {"minLength": 6, "maxLength": 18}
    assert body["data"]["questionLimits"]["maxLength"] == 500
    assert body["data"]["uploadLimits"]["maxFiles"] == 20
    assert body["requestId"].startswith("req_")
    assert response.headers["x-request-id"] == body["requestId"]


def test_health_endpoints(test_context):
    client, _, _ = test_context
    assert client.get("/api/v1/system/health/live").json()["data"]["status"] == "ok"
    assert client.get("/api/v1/system/health/ready").json()["data"]["database"] == "available"


def test_sso_disabled_and_open_redirect_rejected(test_context):
    client, _, base_settings = test_context
    disabled = client.get(
        "/api/v1/auth/sso/authorize",
        params={"channel": "student", "returnTo": "/home"},
        follow_redirects=False,
    )
    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "AUTH_SSO_UNAVAILABLE"

    cas_settings = base_settings.model_copy(
        update={"sso_provider": "cas", "sso_cas_base_url": "https://sso.example.edu/cas"}
    )
    app.dependency_overrides[get_settings] = lambda: cas_settings
    rejected = client.get(
        "/api/v1/auth/sso/authorize",
        params={"channel": "student", "returnTo": "https://evil.example/steal"},
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "AUTH_RETURN_TO_INVALID"
