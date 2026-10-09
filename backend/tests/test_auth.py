from fastapi.testclient import TestClient

from conftest import captcha_value, login


def test_captcha_is_single_use_and_errors_are_stable(test_context):
    client, _, _ = test_context
    captcha_id, captcha_code = captcha_value(client)
    first = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026000001",
            "password": "wrong-password",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
        },
    )
    assert first.status_code == 401
    assert first.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    second = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026000001",
            "password": "OldPassword!123",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
        },
    )
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "AUTH_CAPTCHA_INVALID"


def test_login_me_logout_flow(test_context):
    client, _, settings = test_context
    response = login(client)
    assert response.status_code == 200
    assert response.json()["data"]["tokenType"] == "Bearer"
    assert settings.refresh_cookie_name in response.cookies
    user = response.json()["data"]["user"]
    assert user["name"] == "李明"
    assert user["email"] == "l***@example.edu.cn"
    assert user["phone"] == "138****8000"
    assert user["canAccessAdmin"] is True
    assert "dashboard.read" in user["permissions"]

    token = response.json()["data"]["accessToken"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["department"]["code"] == "ACADEMIC"

    logout = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_refresh_rotation_and_reuse_detection(test_context):
    client, _, settings = test_context
    signed_in = login(client)
    first_access = signed_in.json()["data"]["accessToken"]
    old_refresh = signed_in.cookies.get(settings.refresh_cookie_name)

    rotated = client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    second_access = rotated.json()["data"]["accessToken"]
    assert second_access != first_access
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {first_access}"}
    ).status_code == 401

    replay = TestClient(client.app)
    replay.cookies.set(settings.refresh_cookie_name, old_refresh, path="/api/v1/auth")
    reused = replay.post("/api/v1/auth/refresh")
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "AUTH_REFRESH_INVALID"

    family_revoked = client.post("/api/v1/auth/refresh")
    assert family_revoked.status_code == 401


def test_profile_update_and_password_change_revoke_sessions(test_context):
    client, _, _ = test_context
    signed_in = login(client)
    access = signed_in.json()["data"]["accessToken"]
    auth = {"Authorization": f"Bearer {access}"}

    updated = client.patch(
        "/api/v1/me",
        headers=auth,
        json={"email": "new.address@example.edu.cn", "phone": "+86 13900139000"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["email"] == "n***@example.edu.cn"
    assert updated.json()["data"]["phone"].endswith("9000")

    changed = client.post(
        "/api/v1/me/password",
        headers=auth,
        json={"oldPassword": "OldPassword!123", "newPassword": "abc123"},
    )
    assert changed.status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
    assert login(client, "abc123").status_code == 200


def test_password_length_policy_rejects_values_outside_six_to_eighteen(test_context):
    client, _, _ = test_context
    captcha_id, captcha_code = captcha_value(client)
    too_short = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026000001",
            "password": "12345",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
        },
    )
    assert too_short.status_code == 422
    assert "password" in too_short.json()["error"]["fieldErrors"]

    too_long = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026000001",
            "password": "1234567890123456789",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
        },
    )
    assert too_long.status_code == 422
    assert "password" in too_long.json()["error"]["fieldErrors"]


def test_validation_errors_use_common_envelope(test_context):
    client, _, _ = test_context
    response = client.post("/api/v1/auth/login", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "account" in body["error"]["fieldErrors"]
    assert body["requestId"].startswith("req_")


def test_repeated_failures_lock_known_account(test_context):
    client, _, _ = test_context
    for _ in range(3):
        captcha_id, captcha_code = captcha_value(client)
        failed = client.post(
            "/api/v1/auth/login",
            json={
                "account": "2026000001",
                "password": "definitely-wrong",
                "captchaId": captcha_id,
                "captchaCode": captcha_code,
            },
        )
        assert failed.status_code == 401

    captcha_id, captcha_code = captcha_value(client)
    locked = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026000001",
            "password": "OldPassword!123",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
        },
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "AUTH_ACCOUNT_LOCKED"
