import base64
import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import as_utc, utcnow
from app.modules.auth import service as auth_service
from app.modules.auth.models import AuthSession
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


def test_captcha_svg_keeps_rotated_glyphs_inside_safe_area(test_context, monkeypatch):
    class BoundaryRandom:
        use_upper_bound = False

        def randint(self, lower, upper):
            self.use_upper_bound = not self.use_upper_bound
            return upper if self.use_upper_bound else lower

    monkeypatch.setattr(auth_service.random, "SystemRandom", BoundaryRandom)
    client, _, settings = test_context
    response = client.get("/api/v1/auth/captcha")

    assert response.status_code == 200
    encoded = response.json()["data"]["imageDataUrl"].split(",", 1)[1]
    svg = base64.b64decode(encoded).decode()
    assert 'width="120" height="40" viewBox="0 0 120 40"' in svg
    glyphs = re.findall(
        r'<text x="([\d.]+)" y="(\d+)" transform="rotate\((-?\d+) [^)]+\)">',
        svg,
    )
    assert len(glyphs) == settings.captcha_length
    assert all(8 < float(x) < 112 for x, _, _ in glyphs)
    assert all(27 <= int(y) <= 31 for _, y, _ in glyphs)
    assert all(-12 <= int(angle) <= 12 for _, _, angle in glyphs)


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
    assert "document.create" in user["permissions"]
    assert "system.update" in user["permissions"]
    assert "audit.read" in user["permissions"]
    assert "user.read" not in user["permissions"]

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


def test_login_is_persistent_for_one_month_with_absolute_expiry(test_context):
    client, SessionMaker, settings = test_context
    captcha_id, captcha_code = captcha_value(client)
    signed_in = client.post(
        "/api/v1/auth/login",
        json={
            "account": "2026000001",
            "password": "OldPassword!123",
            "captchaId": captcha_id,
            "captchaCode": captcha_code,
            # Older clients may still send false; the product policy is now
            # consistently one persistent month for every successful login.
            "remember": False,
        },
    )
    assert signed_in.status_code == 200
    with SessionMaker() as db:
        original = db.scalar(select(AuthSession).order_by(AuthSession.created_at.desc()))
        original_expiry = as_utc(original.expires_at)
        remaining = original_expiry - utcnow()
        assert timedelta(days=settings.remember_session_days - 1) < remaining <= timedelta(
            days=settings.remember_session_days
        )

    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    with SessionMaker() as db:
        replacement = db.scalar(
            select(AuthSession).where(AuthSession.revoked_at.is_(None)).order_by(AuthSession.created_at.desc())
        )
        assert abs((as_utc(replacement.expires_at) - original_expiry).total_seconds()) < 1


def test_profile_update_and_password_change_keeps_current_session(test_context):
    client, _, _ = test_context
    signed_in = login(client)
    access = signed_in.json()["data"]["accessToken"]
    auth = {"Authorization": f"Bearer {access}"}
    other_client = TestClient(client.app)
    other_signed_in = login(other_client)
    other_access = other_signed_in.json()["data"]["accessToken"]
    other_auth = {"Authorization": f"Bearer {other_access}"}

    updated = client.patch(
        "/api/v1/me",
        headers=auth,
        json={"email": "new.address@example.edu.cn", "phone": "+86 13900139000"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["email"] == "n***@example.edu.cn"
    assert updated.json()["data"]["phone"].endswith("9000")

    missing_current_password = client.post(
        "/api/v1/me/password",
        headers=auth,
        json={"newPassword": "abc123"},
    )
    assert missing_current_password.status_code == 400
    assert missing_current_password.json()["error"]["code"] == "AUTH_OLD_PASSWORD_INVALID"

    changed = client.post(
        "/api/v1/me/password",
        headers=auth,
        json={"oldPassword": "OldPassword!123", "newPassword": "abc123"},
    )
    assert changed.status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
    assert other_client.get("/api/v1/auth/me", headers=other_auth).status_code == 401
    assert client.post("/api/v1/auth/refresh").status_code == 200
    assert login(client, "abc123").status_code == 200


def test_user_can_upload_view_and_remove_avatar(test_context):
    client, _, _ = test_context
    signed_in = login(client)
    access = signed_in.json()["data"]["accessToken"]
    auth = {"Authorization": f"Bearer {access}"}
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlYQAAAAASUVORK5CYII="
    )

    uploaded = client.post(
        "/api/v1/me/avatar",
        headers=auth,
        files={"file": ("avatar.png", png, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    avatar_url = uploaded.json()["data"]["avatarUrl"]
    assert avatar_url.startswith("/api/v1/me/avatars/")

    avatar = client.get(avatar_url)
    assert avatar.status_code == 200
    assert avatar.headers["content-type"] == "image/png"
    assert avatar.content == png

    invalid = client.post(
        "/api/v1/me/avatar",
        headers=auth,
        files={"file": ("avatar.png", b"not-an-image", "image/png")},
    )
    assert invalid.status_code == 415
    assert invalid.json()["error"]["code"] == "AVATAR_TYPE_UNSUPPORTED"

    removed = client.delete("/api/v1/me/avatar", headers=auth)
    assert removed.status_code == 200
    assert removed.json()["data"]["avatarUrl"] is None
    assert client.get(avatar_url).status_code == 404


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
