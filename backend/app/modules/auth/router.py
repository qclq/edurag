from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentAuth, get_current_auth, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import AppError, success
from app.core.security import as_utc, utcnow
from app.db.session import get_db
from app.integrations.sso.provider import (
    consume_transaction,
    create_authorization_url,
    fetch_cas_identity,
    fetch_oidc_identity,
    frontend_redirect,
    resolve_sso_user,
)
from app.modules.auth.schemas import LoginRequest, SsoChannel
from app.modules.auth.service import (
    authenticate_local,
    client_context,
    create_captcha,
    create_session,
    record_login,
    revoke_session,
    rotate_refresh_token,
)
from app.modules.users.models import User
from app.modules.users.service import user_payload


router = APIRouter()


def set_refresh_cookie(response: Response, token: str, expires_at, settings: Settings) -> None:
    max_age = max(0, int((as_utc(expires_at) - utcnow()).total_seconds()))
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=max_age,
        expires=max_age,
        path=f"{settings.api_prefix}/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=f"{settings.api_prefix}/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/captcha", summary="获取一次性验证码")
def captcha(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.captcha_enabled:
        raise AppError(404, "AUTH_CAPTCHA_DISABLED", "当前登录策略未启用验证码")
    return success(request, create_captcha(db, settings))


@router.post("/login", summary="本地账号登录")
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    user, auth_session, refresh_token, access_token, expires_in = authenticate_local(
        db, payload, request, settings
    )
    response = success(
        request,
        {
            "accessToken": access_token,
            "tokenType": "Bearer",
            "expiresIn": expires_in,
            "user": user_payload(user, db),
        },
    )
    set_refresh_cookie(response, refresh_token, auth_session.expires_at, settings)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/refresh", summary="轮换刷新会话")
def refresh(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    raw_refresh = request.cookies.get(settings.refresh_cookie_name)
    user, auth_session, new_refresh, access_token, expires_in = rotate_refresh_token(
        db, raw_refresh, request, settings
    )
    response = success(
        request,
        {
            "accessToken": access_token,
            "tokenType": "Bearer",
            "expiresIn": expires_in,
            "user": user_payload(user, db),
        },
    )
    set_refresh_cookie(response, new_refresh, auth_session.expires_at, settings)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout", status_code=204, summary="退出当前会话")
def logout(
    auth: CurrentAuth = Depends(get_current_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    revoke_session(db, auth.session)
    response = Response(status_code=204)
    clear_refresh_cookie(response, settings)
    return response


@router.get("/me", summary="获取当前用户和权限")
def me(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response = success(request, user_payload(user, db))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/sso/authorize", summary="发起统一身份认证")
def sso_authorize(
    request: Request,
    channel: SsoChannel = Query(...),
    return_to: str = Query("/home", alias="returnTo", max_length=1000),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    location = create_authorization_url(db, request, channel, return_to, settings)
    return RedirectResponse(location, status_code=302)


@router.get("/sso/callback", name="sso_callback", summary="处理统一身份认证回调")
async def sso_callback(
    request: Request,
    state: str,
    code: str | None = None,
    ticket: str | None = None,
    error: str | None = None,
    error_description: str | None = Query(None, alias="error_description"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if settings.sso_provider == "disabled":
        raise AppError(503, "AUTH_SSO_UNAVAILABLE", "统一身份认证尚未配置")
    transaction = consume_transaction(db, state, settings)
    if error:
        raise AppError(401, "AUTH_SSO_DENIED", error_description or "统一身份认证未完成")
    if settings.sso_provider == "oidc":
        identity = await fetch_oidc_identity(request, transaction, code, settings)
    else:
        identity = await fetch_cas_identity(request, state, ticket, settings)
    user = resolve_sso_user(db, identity, transaction.channel, settings)
    now = utcnow()
    user.last_login_at = now
    ip, user_agent = client_context(request)
    record_login(
        db,
        account=user.account,
        user=user,
        result="success",
        error_code=None,
        ip=ip,
        user_agent=user_agent,
    )
    auth_session, raw_refresh, _, _ = create_session(
        db, user, settings, remember=True, ip=ip, user_agent=user_agent
    )
    db.commit()
    response = RedirectResponse(frontend_redirect(settings, transaction.return_to), status_code=302)
    set_refresh_cookie(response, raw_refresh, auth_session.expires_at, settings)
    response.headers["Cache-Control"] = "no-store"
    return response
