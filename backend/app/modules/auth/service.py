import base64
import html
import random
import secrets
import string
import uuid
from datetime import timedelta

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    as_utc,
    create_access_token,
    new_refresh_token,
    token_digest,
    utcnow,
    verify_password,
)
from app.modules.auth.models import AuthSession, CaptchaChallenge, LoginEvent
from app.modules.auth.schemas import LoginRequest
from app.modules.users.models import Role, User


CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def client_context(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host[:64] if request.client else None
    user_agent = request.headers.get("user-agent")
    return ip, user_agent[:500] if user_agent else None


def create_captcha(db: Session, settings: Settings) -> dict[str, object]:
    code = "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(settings.captcha_length))
    now = utcnow()
    challenge = CaptchaChallenge(
        answer_hash=token_digest(code, settings),
        expires_at=now + timedelta(seconds=settings.captcha_ttl_seconds),
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    rng = random.SystemRandom()
    glyphs = []
    for index, character in enumerate(code):
        x = 18 + index * 25
        y = rng.randint(31, 42)
        angle = rng.randint(-18, 18)
        glyphs.append(
            f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})">{html.escape(character)}</text>'
        )
    lines = "".join(
        f'<line x1="{rng.randint(0, 120)}" y1="{rng.randint(0, 48)}" '
        f'x2="{rng.randint(0, 120)}" y2="{rng.randint(0, 48)}" />'
        for _ in range(5)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="48" viewBox="0 0 120 48">'
        '<rect width="120" height="48" rx="8" fill="#eff4ff"/>'
        '<g stroke="#93b4e8" stroke-width="1" opacity=".65">'
        f"{lines}</g>"
        '<g fill="#174a93" font-family="monospace" font-size="24" font-weight="700">'
        f'{"".join(glyphs)}</g></svg>'
    )
    encoded = base64.b64encode(svg.encode()).decode()
    return {
        "captchaId": str(challenge.id),
        "imageDataUrl": f"data:image/svg+xml;base64,{encoded}",
        "expiresAt": challenge.expires_at,
    }


def verify_captcha(db: Session, captcha_id: str | None, code: str | None, settings: Settings) -> None:
    if not settings.captcha_enabled:
        return
    field_errors = {"captchaCode": "验证码错误或已失效"}
    if not captcha_id or not code:
        raise AppError(400, "AUTH_CAPTCHA_REQUIRED", "请输入验证码", field_errors=field_errors)
    try:
        challenge_uuid = uuid.UUID(captcha_id)
    except ValueError as exc:
        raise AppError(400, "AUTH_CAPTCHA_INVALID", "验证码错误或已失效", field_errors=field_errors) from exc
    challenge = db.scalar(select(CaptchaChallenge).where(CaptchaChallenge.id == challenge_uuid).with_for_update())
    now = utcnow()
    if (
        challenge is None
        or challenge.consumed_at is not None
        or as_utc(challenge.expires_at) <= now
        or challenge.attempts >= settings.captcha_max_attempts
    ):
        raise AppError(400, "AUTH_CAPTCHA_INVALID", "验证码错误或已失效", field_errors=field_errors)
    challenge.attempts += 1
    if not secrets.compare_digest(challenge.answer_hash, token_digest(code.upper(), settings)):
        if challenge.attempts >= settings.captcha_max_attempts:
            challenge.consumed_at = now
        db.commit()
        raise AppError(400, "AUTH_CAPTCHA_INVALID", "验证码错误或已失效", field_errors=field_errors)
    challenge.consumed_at = now
    db.flush()


def record_login(
    db: Session,
    *,
    account: str,
    user: User | None,
    result: str,
    error_code: str | None,
    ip: str | None,
    user_agent: str | None,
) -> None:
    db.add(
        LoginEvent(
            account=account,
            user_id=user.id if user else None,
            result=result,
            error_code=error_code,
            ip=ip,
            user_agent=user_agent,
            occurred_at=utcnow(),
        )
    )


def create_session(
    db: Session,
    user: User,
    settings: Settings,
    *,
    remember: bool,
    ip: str | None,
    user_agent: str | None,
    token_family: uuid.UUID | None = None,
    expires_at=None,
) -> tuple[AuthSession, str, str, int]:
    raw_refresh = new_refresh_token()
    now = utcnow()
    if expires_at is None:
        expires_at = now + (
            timedelta(days=settings.remember_session_days)
            if remember
            else timedelta(hours=settings.refresh_session_hours)
        )
    auth_session = AuthSession(
        user_id=user.id,
        refresh_token_hash=token_digest(raw_refresh, settings),
        token_family=token_family or uuid.uuid4(),
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(auth_session)
    db.flush()
    access_token, expires_in = create_access_token(str(user.id), str(auth_session.id), settings)
    return auth_session, raw_refresh, access_token, expires_in


def authenticate_local(
    db: Session, payload: LoginRequest, request: Request, settings: Settings
) -> tuple[User, AuthSession, str, str, int]:
    verify_captcha(db, payload.captcha_id, payload.captcha_code, settings)
    account = payload.account.strip().lower()
    ip, user_agent = client_context(request)
    window_start = utcnow() - timedelta(minutes=settings.login_failure_window_minutes)
    recent_failures = db.scalar(
        select(func.count(LoginEvent.id)).where(
            LoginEvent.account == account,
            LoginEvent.result == "failed",
            LoginEvent.occurred_at >= window_start,
        )
    ) or 0
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(func.lower(User.account) == account)
    )
    password_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    valid_password = verify_password(payload.password, password_hash)
    now = utcnow()
    if user and user.locked_until and as_utc(user.locked_until) > now:
        record_login(
            db,
            account=account,
            user=user,
            result="failed",
            error_code="AUTH_ACCOUNT_LOCKED",
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        raise AppError(429, "AUTH_ACCOUNT_LOCKED", "账号暂时锁定，请稍后再试")
    if user is None and recent_failures >= settings.login_max_failures:
        db.commit()
        raise AppError(
            429,
            "AUTH_TOO_MANY_ATTEMPTS",
            "登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(settings.account_lock_minutes * 60)},
        )
    if user and user.status != "active":
        record_login(
            db,
            account=account,
            user=user,
            result="failed",
            error_code="AUTH_ACCOUNT_DISABLED",
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        raise AppError(403, "AUTH_ACCOUNT_DISABLED", "账号已被禁用，请联系管理员")
    if user is None or user.auth_source != "local" or not valid_password:
        if user:
            user.failed_login_count = int(recent_failures) + 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.account_lock_minutes)
        record_login(
            db,
            account=account,
            user=user,
            result="failed",
            error_code="AUTH_INVALID_CREDENTIALS",
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        raise AppError(
            401,
            "AUTH_INVALID_CREDENTIALS",
            "账号或密码错误",
            field_errors={"account": "请检查账号或密码"},
        )
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    record_login(
        db,
        account=account,
        user=user,
        result="success",
        error_code=None,
        ip=ip,
        user_agent=user_agent,
    )
    auth_session, raw_refresh, access_token, expires_in = create_session(
        db,
        user,
        settings,
        remember=payload.remember,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return user, auth_session, raw_refresh, access_token, expires_in


def rotate_refresh_token(
    db: Session, raw_refresh: str | None, request: Request, settings: Settings
) -> tuple[User, AuthSession, str, str, int]:
    invalid = AppError(401, "AUTH_REFRESH_INVALID", "刷新会话无效或已过期")
    if not raw_refresh:
        raise invalid
    existing = db.scalar(
        select(AuthSession)
        .options(selectinload(AuthSession.user).selectinload(User.roles).selectinload(Role.permissions))
        .where(AuthSession.refresh_token_hash == token_digest(raw_refresh, settings))
        .with_for_update()
    )
    if existing is None:
        raise invalid
    now = utcnow()
    if existing.revoked_at is not None:
        if existing.revoked_reason == "rotated":
            db.execute(
                update(AuthSession)
                .where(AuthSession.token_family == existing.token_family, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoked_reason="reuse_detected")
            )
            db.commit()
        raise invalid
    if as_utc(existing.expires_at) <= now:
        existing.revoked_at = now
        existing.revoked_reason = "expired"
        db.commit()
        raise invalid
    user = existing.user
    if user.status != "active":
        existing.revoked_at = now
        existing.revoked_reason = "user_inactive"
        db.commit()
        raise invalid
    ip, user_agent = client_context(request)
    replacement, new_raw, access_token, expires_in = create_session(
        db,
        user,
        settings,
        remember=False,
        ip=ip,
        user_agent=user_agent,
        token_family=existing.token_family,
        expires_at=existing.expires_at,
    )
    existing.revoked_at = now
    existing.revoked_reason = "rotated"
    existing.replaced_by_id = replacement.id
    existing.last_used_at = now
    db.commit()
    return user, replacement, new_raw, access_token, expires_in


def revoke_session(db: Session, auth_session: AuthSession, reason: str = "logout") -> None:
    if auth_session.revoked_at is None:
        auth_session.revoked_at = utcnow()
        auth_session.revoked_reason = reason
        db.commit()
