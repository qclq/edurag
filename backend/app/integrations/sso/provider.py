import secrets
import xml.etree.ElementTree as ET
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import as_utc, pkce_pair, token_digest, utcnow
from app.modules.auth.models import SsoTransaction
from app.modules.auth.service import client_context, create_session, record_login
from app.modules.users.models import Role, User


def validate_return_to(return_to: str) -> str:
    if not return_to.startswith("/") or return_to.startswith("//") or "\\" in return_to:
        raise AppError(400, "AUTH_RETURN_TO_INVALID", "returnTo 必须是站内相对路径")
    if any(character in return_to for character in ("\r", "\n")):
        raise AppError(400, "AUTH_RETURN_TO_INVALID", "returnTo 包含非法字符")
    return return_to


def callback_url(request: Request) -> str:
    return str(request.url_for("sso_callback"))


def create_authorization_url(
    db: Session, request: Request, channel: str, return_to: str, settings: Settings
) -> str:
    if settings.sso_provider == "disabled":
        raise AppError(503, "AUTH_SSO_UNAVAILABLE", "统一身份认证尚未配置")
    return_to = validate_return_to(return_to)
    state = secrets.token_urlsafe(32)
    verifier = None
    challenge = None
    if settings.sso_provider == "oidc":
        verifier, challenge = pkce_pair()
    transaction = SsoTransaction(
        state_hash=token_digest(state, settings),
        channel=channel,
        return_to=return_to,
        code_verifier=verifier,
        expires_at=utcnow() + timedelta(minutes=5),
    )
    db.add(transaction)
    db.commit()
    redirect_uri = callback_url(request)
    if settings.sso_provider == "oidc":
        params = {
            "client_id": settings.sso_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": settings.sso_scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{settings.sso_authorize_url}?{urlencode(params)}"
    service = f"{redirect_uri}?{urlencode({'state': state})}"
    return f"{str(settings.sso_cas_base_url).rstrip('/')}/login?{urlencode({'service': service})}"


def consume_transaction(db: Session, state: str, settings: Settings) -> SsoTransaction:
    transaction = db.scalar(
        select(SsoTransaction)
        .where(SsoTransaction.state_hash == token_digest(state, settings))
        .with_for_update()
    )
    if (
        transaction is None
        or transaction.consumed_at is not None
        or as_utc(transaction.expires_at) <= utcnow()
    ):
        raise AppError(400, "AUTH_SSO_STATE_INVALID", "统一认证请求已失效，请重新发起登录")
    transaction.consumed_at = utcnow()
    db.commit()
    return transaction


async def fetch_oidc_identity(
    request: Request, transaction: SsoTransaction, code: str | None, settings: Settings
) -> dict[str, str | None]:
    if not code:
        raise AppError(400, "AUTH_SSO_CALLBACK_INVALID", "统一认证回调缺少授权码")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url(request),
        "client_id": settings.sso_client_id,
        "code_verifier": transaction.code_verifier,
    }
    if settings.sso_client_secret:
        form["client_secret"] = settings.sso_client_secret
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            token_response = await client.post(str(settings.sso_token_url), data=form)
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise ValueError("missing access_token")
            user_response = await client.get(
                str(settings.sso_userinfo_url), headers={"Authorization": f"Bearer {access_token}"}
            )
            user_response.raise_for_status()
            claims = user_response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise AppError(503, "AUTH_SSO_PROVIDER_ERROR", "统一身份认证服务暂时不可用") from exc
    subject = claims.get("sub")
    if not subject:
        raise AppError(502, "AUTH_SSO_IDENTITY_INVALID", "统一认证未返回用户标识")
    account = claims.get("preferred_username") or claims.get("student_or_staff_no") or claims.get("email")
    if isinstance(account, str) and "@" in account:
        account = account.split("@", 1)[0]
    return {
        "subject": str(subject),
        "account": str(account or subject),
        "name": str(claims.get("name") or account or subject),
        "email": str(claims["email"]) if claims.get("email") else None,
        "student_or_staff_no": (
            str(claims["student_or_staff_no"]) if claims.get("student_or_staff_no") else str(account or subject)
        ),
    }


async def fetch_cas_identity(
    request: Request, state: str, ticket: str | None, settings: Settings
) -> dict[str, str | None]:
    if not ticket:
        raise AppError(400, "AUTH_SSO_CALLBACK_INVALID", "统一认证回调缺少票据")
    service = f"{callback_url(request)}?{urlencode({'state': state})}"
    validate_url = f"{str(settings.sso_cas_base_url).rstrip('/')}/serviceValidate"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(validate_url, params={"service": service, "ticket": ticket})
            response.raise_for_status()
        root = ET.fromstring(response.text)
        namespace = {"cas": "http://www.yale.edu/tp/cas"}
        user_node = root.find(".//cas:authenticationSuccess/cas:user", namespace)
        if user_node is None or not user_node.text:
            raise ValueError("CAS validation failed")
        account = user_node.text.strip()
        attributes = root.find(".//cas:authenticationSuccess/cas:attributes", namespace)
        values = {child.tag.split("}")[-1]: child.text for child in attributes or []}
    except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
        raise AppError(503, "AUTH_SSO_PROVIDER_ERROR", "统一身份认证服务暂时不可用") from exc
    return {
        "subject": account,
        "account": account,
        "name": values.get("displayName") or values.get("name") or account,
        "email": values.get("email"),
        "student_or_staff_no": values.get("employeeNumber") or values.get("studentNumber") or account,
    }


def resolve_sso_user(
    db: Session, identity: dict[str, str | None], channel: str, settings: Settings
) -> User:
    subject = identity["subject"]
    account = str(identity["account"]).lower()
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(
            or_(
                (User.auth_source == settings.sso_provider) & (User.external_subject == subject),
                (User.auth_source == settings.sso_provider) & (User.account == account),
            )
        )
    )
    conflicting = db.scalar(select(User).where(User.account == account))
    if user is None and conflicting is not None:
        raise AppError(409, "AUTH_SSO_ACCOUNT_CONFLICT", "统一认证账号与已有账号冲突，请联系管理员")
    if user is None:
        if not settings.sso_auto_provision:
            raise AppError(403, "AUTH_SSO_USER_NOT_PROVISIONED", "账号尚未开通，请联系管理员")
        normal_role = db.scalar(select(Role).where(Role.code == "normal_user"))
        if normal_role is None:
            normal_role = Role(code="normal_user", name="普通用户", is_system=True)
            db.add(normal_role)
        user = User(
            account=account,
            name=str(identity["name"]),
            student_or_staff_no=identity["student_or_staff_no"],
            user_type=channel,
            email=identity["email"],
            status="active",
            auth_source=settings.sso_provider,
            external_subject=subject,
            roles=[normal_role],
        )
        db.add(user)
        db.flush()
    else:
        user.name = str(identity["name"])
        user.email = identity["email"]
        user.student_or_staff_no = identity["student_or_staff_no"]
        user.user_type = channel
    if user.status != "active":
        raise AppError(403, "AUTH_ACCOUNT_DISABLED", "账号已被禁用，请联系管理员")
    return user


def frontend_redirect(settings: Settings, return_to: str) -> str:
    base = settings.frontend_url.rstrip("/")
    target = f"{base}{return_to}"
    split = urlsplit(target)
    query = split.query
    query = f"{query}&sso=success" if query else "sso=success"
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))
