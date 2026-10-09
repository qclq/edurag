from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import success
from app.db.session import get_db
from app.modules.auth.router import clear_refresh_cookie
from app.modules.users.models import User
from app.modules.users.schemas import PasswordChange, ProfileUpdate
from app.modules.users.service import change_password, update_profile, user_payload


router = APIRouter()


@router.patch("", summary="更新当前用户可编辑资料")
def patch_me(
    payload: ProfileUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updated = update_profile(db, user, payload)
    return success(request, user_payload(updated, db))


@router.post("/password", status_code=204, summary="修改本地账号密码")
def password(
    payload: PasswordChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    change_password(db, user, payload)
    response = Response(status_code=204)
    clear_refresh_cookie(response, settings)
    return response
