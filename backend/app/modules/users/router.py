import uuid

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentAuth, get_current_auth, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import AppError, success
from app.db.session import get_db
from app.integrations.storage import get_storage
from app.modules.users.models import User
from app.modules.users.schemas import PasswordChange, ProfileUpdate
from app.modules.users.service import (
    AVATAR_MAX_BYTES,
    avatar_storage_details,
    change_password,
    remove_avatar,
    save_avatar,
    update_profile,
    user_payload,
)


router = APIRouter()


def _stream_avatar(stream):
    try:
        while chunk := stream.read(64 * 1024):
            yield chunk
    finally:
        stream.close()


@router.get("/avatars/{user_id}/{file_name}", include_in_schema=False)
def get_avatar(
    user_id: uuid.UUID,
    file_name: str,
    settings: Settings = Depends(get_settings),
):
    storage_key, mime_type = avatar_storage_details(user_id, file_name)
    try:
        stream = get_storage(settings).open(storage_key)
    except (FileNotFoundError, OSError) as exc:
        raise AppError(404, "AVATAR_NOT_FOUND", "头像不存在") from exc
    return StreamingResponse(
        _stream_avatar(stream),
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/avatar", summary="上传当前用户头像")
async def post_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    content = await file.read(AVATAR_MAX_BYTES + 1)
    updated = save_avatar(db, user, content, settings)
    return success(request, user_payload(updated, db))


@router.delete("/avatar", summary="移除当前用户头像")
def delete_avatar(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    updated = remove_avatar(db, user, settings)
    return success(request, user_payload(updated, db))


@router.patch("", summary="更新当前用户可编辑资料")
def patch_me(
    payload: ProfileUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updated = update_profile(db, user, payload)
    return success(request, user_payload(updated, db))


@router.post("/password", summary="修改本地账号密码")
def password(
    payload: PasswordChange,
    request: Request,
    auth: CurrentAuth = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    change_password(db, auth.user, payload, current_session=auth.session)
    response = success(request, user_payload(auth.user, db))
    response.headers["Cache-Control"] = "no-store"
    return response
