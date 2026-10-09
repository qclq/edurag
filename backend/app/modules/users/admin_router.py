from __future__ import annotations

import csv
import io
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_page_params, require_permissions
from app.core.errors import AppError, paginated, success
from app.core.security import as_utc, hash_password, utcnow
from app.db.session import get_db
from app.modules.auth.models import AuthSession
from app.modules.operations.models import UserImport
from app.modules.operations.schemas import (
    AdminUserUpdate,
    ImportCommit,
    PasswordReset,
    UserCreate,
    UserExportCreate,
    UserRolesUpdate,
    UserStatusUpdate,
)
from app.modules.operations.service import record_audit
from app.modules.tasks.service import create_task, task_payload, update_task
from app.modules.users.models import Department, Permission, Role, User
from app.modules.users.service import user_payload


router = APIRouter()


def admin_user_payload(db: Session, user: User) -> dict:
    data = user_payload(user, db)
    data["email"] = user.email
    data["phone"] = user.phone
    data["version"] = user.row_version
    return data


def _get_user(db: Session, user_id) -> User:
    user = db.scalar(
        select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == user_id)
    )
    if not user:
        raise AppError(404, "USER_NOT_FOUND", "用户不存在")
    return user


def _roles(db: Session, codes: list[str]) -> list[Role]:
    unique = sorted(set(codes))
    roles = db.scalars(select(Role).options(selectinload(Role.permissions)).where(Role.code.in_(unique))).unique().all()
    if len(roles) != len(unique):
        raise AppError(400, "ROLE_INVALID", "部分角色不存在")
    return list(roles)


@router.get("/users", summary="用户分页")
def list_users(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    user_type: str | None = Query(default=None, alias="userType"),
    department_id: uuid.UUID | None = Query(default=None, alias="departmentId"),
    role: str | None = None,
    status: str | None = None,
    page: PageParams = Depends(get_page_params),
    current: User = Depends(require_permissions("user.read")),
    db: Session = Depends(get_db),
):
    statement = select(User).options(selectinload(User.roles).selectinload(Role.permissions)).order_by(User.created_at.desc())
    if q:
        pattern = f"%{q}%"
        statement = statement.where(or_(User.name.ilike(pattern), User.account.ilike(pattern), User.student_or_staff_no.ilike(pattern)))
    if user_type:
        statement = statement.where(User.user_type == user_type)
    if department_id:
        statement = statement.where(User.department_id == department_id)
    if role:
        statement = statement.where(User.roles.any(code=role))
    if status:
        statement = statement.where(User.status == status)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.offset(page.offset).limit(page.page_size)).unique().all()
    return paginated(request, [admin_user_payload(db, item) for item in items], page=page.page, page_size=page.page_size, total=total)


@router.get("/users/stats", summary="用户统计")
def user_stats(
    request: Request,
    current: User = Depends(require_permissions("user.read")),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User)).all()
    return success(request, {"total": len(users), "students": sum(item.user_type == "student" for item in users), "staff": sum(item.user_type == "staff" for item in users), "active": sum(item.status == "active" for item in users), "abnormal": sum(item.status != "active" or item.locked_until is not None for item in users)})


@router.post("/users", status_code=201, summary="新建用户")
def create_user(
    payload: UserCreate,
    request: Request,
    current: User = Depends(require_permissions("user.create")),
    db: Session = Depends(get_db),
):
    if payload.department_id and not db.get(Department, payload.department_id):
        raise AppError(400, "DEPARTMENT_INVALID", "部门不存在")
    user = User(
        name=payload.name,
        account=payload.account,
        student_or_staff_no=payload.student_or_staff_no,
        user_type=payload.user_type,
        department_id=payload.department_id,
        email=payload.email,
        phone=payload.phone,
        status=payload.status,
        auth_source="local",
        password_hash=hash_password(secrets.token_urlsafe(32)),
        roles=_roles(db, payload.role_codes),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "USER_DUPLICATE", "账号或学工号已存在") from exc
    record_audit(db, actor=current, module="user", action="create", target_type="user", target_id=user.id, request=request, after=payload.model_dump(exclude={"role_codes"}))
    db.commit()
    return success(request, admin_user_payload(db, _get_user(db, user.id)), status_code=201)


@router.get("/users/{user_id}", summary="用户详情")
def get_user(
    user_id: uuid.UUID,
    request: Request,
    current: User = Depends(require_permissions("user.read")),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    data = admin_user_payload(db, user)
    data["loginOverview"] = {"lastLoginAt": user.last_login_at, "lastActiveAt": user.last_active_at, "lockedUntil": user.locked_until, "failedLoginCount": user.failed_login_count}
    return success(request, data)


@router.patch("/users/{user_id}", summary="编辑用户")
def patch_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    request: Request,
    current: User = Depends(require_permissions("user.update")),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    if user.row_version != payload.version:
        raise AppError(409, "VERSION_CONFLICT", "用户资料已被修改，请刷新后重试")
    before = admin_user_payload(db, user)
    for field in ("name", "student_or_staff_no", "user_type", "department_id", "email", "phone"):
        if field in payload.model_fields_set:
            setattr(user, field, getattr(payload, field))
    user.row_version += 1
    record_audit(db, actor=current, module="user", action="update", target_type="user", target_id=user.id, request=request, before=before, after=payload.model_dump())
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "USER_DUPLICATE", "学工号已存在") from exc
    return success(request, admin_user_payload(db, _get_user(db, user.id)))


@router.post("/users/{user_id}/status", summary="启停用户")
def user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    request: Request,
    current: User = Depends(require_permissions("user.status")),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    if user.id == current.id and payload.status == "disabled":
        raise AppError(409, "USER_SELF_DISABLE_FORBIDDEN", "不能停用当前登录账号")
    user.status = payload.status
    user.row_version += 1
    if payload.status == "disabled":
        for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
            session.revoked_at = utcnow()
            session.revoked_reason = "user_disabled"
    record_audit(db, actor=current, module="user", action="status", target_type="user", target_id=user.id, request=request, after=payload.model_dump())
    db.commit()
    return success(request, {"id": str(user.id), "status": user.status, "version": user.row_version})


@router.put("/users/{user_id}/roles", summary="分配用户角色")
def user_roles(
    user_id: uuid.UUID,
    payload: UserRolesUpdate,
    request: Request,
    current: User = Depends(require_permissions("role.assign")),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    if user.row_version != payload.version:
        raise AppError(409, "VERSION_CONFLICT", "用户角色已被修改，请刷新后重试")
    before = [role.code for role in user.roles]
    user.roles = _roles(db, payload.role_codes)
    user.row_version += 1
    record_audit(db, actor=current, module="user", action="roles.update", target_type="user", target_id=user.id, request=request, before=before, after=payload.model_dump())
    db.commit()
    return success(request, {"id": str(user.id), "roles": [{"code": role.code, "name": role.name} for role in user.roles], "version": user.row_version})


@router.post("/users/{user_id}/password-reset", summary="重置本地密码")
def password_reset(
    user_id: uuid.UUID,
    payload: PasswordReset,
    request: Request,
    current: User = Depends(require_permissions("user.password.reset")),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    if user.auth_source != "local":
        raise AppError(409, "AUTH_PASSWORD_MANAGED_EXTERNALLY", "该账号密码由统一身份认证管理")
    if payload.delivery_method == "email" and not user.email:
        raise AppError(400, "PASSWORD_DELIVERY_UNAVAILABLE", "用户没有可用邮箱")
    if payload.delivery_method == "sms" and not user.phone:
        raise AppError(400, "PASSWORD_DELIVERY_UNAVAILABLE", "用户没有可用手机号")
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.row_version += 1
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
        session.revoked_at = utcnow()
        session.revoked_reason = "admin_password_reset"
    record_audit(db, actor=current, module="user", action="password.reset", target_type="user", target_id=user.id, request=request, after={"deliveryMethod": payload.delivery_method})
    db.commit()
    return success(request, {"status": "delivery_queued", "deliveryMethod": payload.delivery_method, "credentialReturned": False})


@router.get("/roles", summary="角色与权限")
def roles(
    request: Request,
    current: User = Depends(require_permissions("user.read")),
    db: Session = Depends(get_db),
):
    items = db.scalars(select(Role).options(selectinload(Role.permissions)).order_by(Role.code)).unique().all()
    return success(request, [{"id": str(item.id), "code": item.code, "name": item.name, "description": item.description, "system": item.is_system, "permissions": sorted(permission.code for permission in item.permissions)} for item in items])


@router.get("/departments", summary="部门列表")
def departments(
    request: Request,
    q: str | None = None,
    parent_id: uuid.UUID | None = Query(default=None, alias="parentId"),
    current: User = Depends(require_permissions("user.read", "kb.read", any_of=True)),
    db: Session = Depends(get_db),
):
    statement = select(Department).order_by(Department.name)
    if q:
        statement = statement.where(or_(Department.name.ilike(f"%{q}%"), Department.code.ilike(f"%{q}%")))
    if parent_id:
        statement = statement.where(Department.parent_id == parent_id)
    items = db.scalars(statement).all()
    return success(request, [{"id": str(item.id), "parentId": str(item.parent_id) if item.parent_id else None, "name": item.name, "code": item.code, "status": item.status} for item in items])


def _parse_import(file_name: str, content: bytes) -> list[dict[str, str]]:
    extension = file_name.rsplit(".", 1)[-1].lower()
    if extension == "csv":
        text = content.decode("utf-8-sig")
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    if extension == "xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise AppError(503, "XLSX_SUPPORT_UNAVAILABLE", "服务未安装 XLSX 解析依赖") from exc
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(value or "").strip() for value in rows[0]]
        return [{headers[index]: str(value or "").strip() for index, value in enumerate(row)} for row in rows[1:]]
    raise AppError(415, "USER_IMPORT_TYPE_UNSUPPORTED", "仅支持 CSV 或 XLSX")


@router.post("/user-imports/validate", status_code=201, summary="用户导入预校验")
async def validate_import(
    request: Request,
    file: UploadFile = File(...),
    current: User = Depends(require_permissions("user.import")),
    db: Session = Depends(get_db),
):
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise AppError(413, "USER_IMPORT_TOO_LARGE", "导入文件不能超过 10MB")
    rows = _parse_import(file.filename or "", content)
    if len(rows) > 10_000:
        raise AppError(400, "USER_IMPORT_TOO_MANY_ROWS", "单次最多导入 10000 行")
    errors, conflicts, normalized = [], [], []
    for index, row in enumerate(rows, 2):
        account = (row.get("account") or row.get("账号") or "").strip()
        name = (row.get("name") or row.get("姓名") or "").strip()
        if not account or not name:
            errors.append({"row": index, "message": "账号和姓名不能为空"})
            continue
        values = {"row": index, "account": account, "name": name, "studentOrStaffNo": row.get("studentOrStaffNo") or row.get("学工号") or None, "userType": row.get("userType") or row.get("类型") or "staff", "email": row.get("email") or row.get("邮箱") or None, "phone": row.get("phone") or row.get("手机") or None}
        existing = db.scalar(select(User).where(User.account == account))
        if existing:
            conflicts.append({"row": index, "account": account, "existingUserId": str(existing.id), "type": "account_exists"})
        normalized.append(values)
    summary = {"total": len(rows), "valid": len(normalized), "conflicts": len(conflicts), "errors": len(errors)}
    item = UserImport(created_by=current.id, file_name=file.filename or "import", rows_json=normalized, summary_json={**summary, "conflicts": conflicts, "errorsDetail": errors}, expires_at=utcnow() + timedelta(hours=24))
    db.add(item)
    db.commit()
    return success(request, {"importId": str(item.id), "summary": summary, "conflicts": conflicts, "errors": errors, "expiresAt": item.expires_at}, status_code=201)


@router.post("/user-imports/{import_id}/commit", status_code=202, summary="提交用户导入")
def commit_import(
    import_id: uuid.UUID,
    payload: ImportCommit,
    request: Request,
    current: User = Depends(require_permissions("user.import")),
    db: Session = Depends(get_db),
):
    item = db.scalar(select(UserImport).where(UserImport.id == import_id, UserImport.created_by == current.id))
    if not item or as_utc(item.expires_at) <= utcnow():
        raise AppError(404, "USER_IMPORT_NOT_FOUND", "导入记录不存在或已过期")
    if item.status != "validated":
        raise AppError(409, "USER_IMPORT_ALREADY_COMMITTED", "导入记录已提交")
    decisions = {decision.row: decision for decision in payload.decisions}
    normal_role = db.scalar(select(Role).where(Role.code == "normal_user"))
    task, _ = create_task(db, type="user_import", created_by=current.id, progress_text="正在导入用户")
    update_task(db, task, status="running", stage="importing", progress_percent=10, progress_text="正在导入用户")
    created_count = updated_count = skipped_count = 0
    for row in item.rows_json:
        decision = decisions.get(row["row"])
        if decision and decision.action == "exclude":
            skipped_count += 1
            continue
        values = {**row, **(decision.values or {})} if decision and decision.action == "edit" else row
        user = db.scalar(select(User).where(User.account == values["account"]))
        if user:
            if not decision or decision.action != "update":
                skipped_count += 1
                continue
            user.name = values["name"]
            user.email = values.get("email")
            user.phone = values.get("phone")
            user.row_version += 1
            updated_count += 1
        else:
            user = User(account=values["account"], name=values["name"], student_or_staff_no=values.get("studentOrStaffNo"), user_type=values.get("userType") or "staff", email=values.get("email"), phone=values.get("phone"), auth_source="local", password_hash=hash_password(secrets.token_urlsafe(32)), roles=[normal_role] if normal_role else [])
            db.add(user)
            created_count += 1
    item.status = "committed"
    item.committed_at = utcnow()
    update_task(db, task, status="succeeded", stage="completed", progress_percent=100, progress_text="用户导入完成")
    record_audit(db, actor=current, module="user", action="import", target_type="user_import", target_id=item.id, request=request, after={"created": created_count, "updated": updated_count, "skipped": skipped_count})
    db.commit()
    data = task_payload(task, include_events=False)
    data["result"] = {"created": created_count, "updated": updated_count, "skipped": skipped_count}
    return success(request, data, status_code=202)


@router.post("/user-exports", status_code=202, summary="导出用户")
def export_users(
    payload: UserExportCreate,
    request: Request,
    current: User = Depends(require_permissions("user.export")),
    db: Session = Depends(get_db),
):
    task, _ = create_task(db, type="user_export", created_by=current.id, progress_text="等待导出用户")
    record_audit(db, actor=current, module="user", action="export", target_type="task", target_id=task.id, request=request, after=payload.filters)
    db.commit()
    return success(request, task_payload(task, include_events=False), status_code=202)
