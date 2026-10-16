from __future__ import annotations

import csv
import io
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PageParams, get_page_params, is_super_admin, require_permissions, require_super_admin
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

DEFAULT_INITIAL_PASSWORD = "123456"
ADMIN_ROLE_CODES = {"knowledge_admin", "user_admin", "super_admin"}
CREATABLE_STAFF_ROLE_CODES = {"normal_user", "knowledge_admin"}


def admin_user_payload(db: Session, user: User) -> dict:
    data = user_payload(user, db)
    data["email"] = user.email
    data["phone"] = user.phone
    data["version"] = user.row_version
    return data


def _get_user(db: Session, user_id) -> User:
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id, User.status != "deleted", User.auth_source != "guest")
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


def _ensure_role_assignment_allowed(actor: User, user_type: str, roles: list[Role]) -> None:
    role_codes = {role.code for role in roles}
    if user_type in {"student", "external"} and role_codes != {"normal_user"}:
        label = "学生" if user_type == "student" else "外部人员"
        raise AppError(400, "USER_ROLE_INVALID", f"{label}账号只能分配普通用户角色")
    if ADMIN_ROLE_CODES.intersection(role_codes) and not is_super_admin(actor):
        raise AppError(403, "ADMIN_ROLE_ASSIGN_FORBIDDEN", "只有超级管理员可以授予管理员角色")


def _ensure_password_reset_allowed(actor: User) -> None:
    actor_roles = {role.code for role in actor.roles}
    if not ADMIN_ROLE_CODES.intersection(actor_roles):
        raise AppError(403, "PASSWORD_RESET_ADMIN_REQUIRED", "只有管理员可以重置用户密码")


def _ensure_target_management_allowed(actor: User, target: User) -> None:
    target_roles = {role.code for role in target.roles}
    if ADMIN_ROLE_CODES.intersection(target_roles) and not is_super_admin(actor):
        raise AppError(403, "ADMIN_ACCOUNT_MANAGEMENT_FORBIDDEN", "只有超级管理员可以管理管理员账号")


@router.get("/users", summary="用户分页")
def list_users(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    user_type: str | None = Query(default=None, alias="userType"),
    department_id: uuid.UUID | None = Query(default=None, alias="departmentId"),
    role: str | None = None,
    status: str | None = None,
    page: PageParams = Depends(get_page_params),
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    statement = (
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.status != "deleted", User.auth_source != "guest")
        .order_by(User.created_at.desc())
    )
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
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    users = db.scalars(select(User).where(User.status != "deleted", User.auth_source != "guest")).all()
    return success(request, {"total": len(users), "students": sum(item.user_type == "student" for item in users), "staff": sum(item.user_type == "staff" for item in users), "external": sum(item.user_type == "external" for item in users), "active": sum(item.status == "active" for item in users), "abnormal": sum(item.status != "active" or item.locked_until is not None for item in users)})


@router.post("/users", status_code=201, summary="新建用户")
def create_user(
    payload: UserCreate,
    request: Request,
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if payload.user_type == "external":
        raise AppError(400, "EXTERNAL_USER_CREATION_DISABLED", "外部人员请使用登录页访客访问入口")
    name = payload.name.strip()
    student_or_staff_no = (payload.student_or_staff_no or "").strip() or None
    account = (payload.account or "").strip().lower()
    if payload.user_type == "student":
        if not student_or_staff_no:
            raise AppError(
                400,
                "STUDENT_NUMBER_REQUIRED",
                "新建学生账号必须填写学号",
                field_errors={"studentOrStaffNo": "请填写学号"},
            )
        account = student_or_staff_no
    elif not account:
        raise AppError(
            400,
            "ACCOUNT_REQUIRED",
            "新建教职工或外部人员账号必须填写登录账号",
            field_errors={"account": "请填写登录账号"},
        )
    if payload.department_id and not db.get(Department, payload.department_id):
        raise AppError(400, "DEPARTMENT_INVALID", "部门不存在")

    requested_role_codes = set(payload.role_codes)
    if payload.user_type in {"student", "external"}:
        requested_role_codes = {"normal_user"}
    elif requested_role_codes not in ({"normal_user"}, {"knowledge_admin"}):
        raise AppError(400, "STAFF_ROLE_INVALID", "教职工账号权限只能选择普通账户或管理员账户")
    assigned_roles = _roles(db, sorted(requested_role_codes))
    _ensure_role_assignment_allowed(current, payload.user_type, assigned_roles)
    user = User(
        name=name,
        account=account,
        student_or_staff_no=student_or_staff_no,
        user_type=payload.user_type,
        department_id=payload.department_id,
        email=payload.email,
        phone=payload.phone,
        status=payload.status,
        auth_source="local",
        password_hash=hash_password(DEFAULT_INITIAL_PASSWORD),
        must_change_password=True,
        roles=assigned_roles,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "USER_DUPLICATE", "账号或学工号已存在") from exc
    record_audit(
        db,
        actor=current,
        module="user",
        action="create",
        target_type="user",
        target_id=user.id,
        request=request,
        after={
            "name": user.name,
            "account": account,
            "studentOrStaffNo": student_or_staff_no,
            "userType": payload.user_type,
            "roleCodes": sorted(requested_role_codes),
        },
    )
    db.commit()
    return success(request, admin_user_payload(db, _get_user(db, user.id)), status_code=201)


@router.get("/users/{user_id}", summary="用户详情")
def get_user(
    user_id: uuid.UUID,
    request: Request,
    current: User = Depends(require_super_admin),
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
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    _ensure_target_management_allowed(current, user)
    if payload.user_type == "external":
        raise AppError(400, "EXTERNAL_USER_CREATION_DISABLED", "外部人员请使用登录页访客访问入口")
    if user.row_version != payload.version:
        raise AppError(409, "VERSION_CONFLICT", "用户资料已被修改，请刷新后重试")
    before = admin_user_payload(db, user)
    for field in ("name", "student_or_staff_no", "user_type", "department_id", "email", "phone"):
        if field in payload.model_fields_set:
            setattr(user, field, getattr(payload, field))
    if user.user_type == "student":
        student_no = (user.student_or_staff_no or "").strip()
        if not student_no:
            raise AppError(400, "STUDENT_NUMBER_REQUIRED", "学生账号必须保留学号")
        user.account = student_no
        user.student_or_staff_no = student_no
        user.roles = _roles(db, ["normal_user"])
    elif user.user_type == "external":
        user.student_or_staff_no = None
        user.department_id = None
        user.roles = _roles(db, ["normal_user"])
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
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    _ensure_target_management_allowed(current, user)
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


@router.delete("/users/{user_id}", status_code=204, summary="删除用户")
def delete_user(
    user_id: uuid.UUID,
    request: Request,
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    if user_id == current.id:
        raise AppError(409, "USER_SELF_DELETE_FORBIDDEN", "不能删除当前登录账号")
    user = _get_user(db, user_id)
    before = admin_user_payload(db, user)
    for session in db.scalars(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
    ):
        session.revoked_at = utcnow()
        session.revoked_reason = "user_deleted"
    user.account = f"deleted-{user.id.hex}"
    user.password_hash = None
    user.name = "已删除用户"
    user.student_or_staff_no = None
    user.subtitle = None
    user.department_id = None
    user.email = None
    user.phone = None
    user.avatar_url = None
    user.external_subject = None
    user.status = "deleted"
    user.locked_until = None
    user.failed_login_count = 0
    user.must_change_password = False
    user.roles = []
    user.row_version += 1
    record_audit(
        db,
        actor=current,
        module="user",
        action="delete",
        target_type="user",
        target_id=user.id,
        request=request,
        before=before,
        after={"status": "deleted"},
    )
    db.commit()
    return Response(status_code=204)


@router.put("/users/{user_id}/roles", summary="分配用户角色")
def user_roles(
    user_id: uuid.UUID,
    payload: UserRolesUpdate,
    request: Request,
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = _get_user(db, user_id)
    _ensure_target_management_allowed(current, user)
    if user.row_version != payload.version:
        raise AppError(409, "VERSION_CONFLICT", "用户角色已被修改，请刷新后重试")
    before = [role.code for role in user.roles]
    assigned_roles = _roles(db, payload.role_codes)
    _ensure_role_assignment_allowed(current, user.user_type, assigned_roles)
    user.roles = assigned_roles
    user.row_version += 1
    record_audit(db, actor=current, module="user", action="roles.update", target_type="user", target_id=user.id, request=request, before=before, after=payload.model_dump())
    db.commit()
    return success(request, {"id": str(user.id), "roles": [{"code": role.code, "name": role.name} for role in user.roles], "version": user.row_version})


@router.post("/users/{user_id}/password-reset", summary="重置本地密码")
def password_reset(
    user_id: uuid.UUID,
    payload: PasswordReset,
    request: Request,
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    _ensure_password_reset_allowed(current)
    user = _get_user(db, user_id)
    _ensure_target_management_allowed(current, user)
    if user.auth_source != "local":
        raise AppError(409, "AUTH_PASSWORD_MANAGED_EXTERNALLY", "该账号密码由统一身份认证管理")
    if payload.delivery_method == "email" and not user.email:
        raise AppError(400, "PASSWORD_DELIVERY_UNAVAILABLE", "用户没有可用邮箱")
    if payload.delivery_method == "sms" and not user.phone:
        raise AppError(400, "PASSWORD_DELIVERY_UNAVAILABLE", "用户没有可用手机号")
    if user.user_type == "student":
        if payload.delivery_method != "manual":
            raise AppError(400, "STUDENT_PASSWORD_RESET_MANUAL_ONLY", "学生密码重置仅支持恢复为统一初始密码")
        user.password_hash = hash_password(DEFAULT_INITIAL_PASSWORD)
        user.must_change_password = True
    else:
        user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.row_version += 1
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))):
        session.revoked_at = utcnow()
        session.revoked_reason = "admin_password_reset"
    record_audit(db, actor=current, module="user", action="password.reset", target_type="user", target_id=user.id, request=request, after={"deliveryMethod": payload.delivery_method})
    db.commit()
    return success(
        request,
        {
            "status": "reset_to_default" if user.user_type == "student" else "delivery_queued",
            "deliveryMethod": payload.delivery_method,
            "credentialReturned": False,
            "mustChangePassword": user.must_change_password,
        },
    )


@router.get("/roles", summary="角色与权限")
def roles(
    request: Request,
    current: User = Depends(require_super_admin),
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


def _parse_import(file_name: str, content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    extension = file_name.rsplit(".", 1)[-1].lower()
    if extension == "csv":
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        headers = [str(value or "").strip() for value in (reader.fieldnames or [])]
        return headers, [{str(key or "").strip(): str(value or "").strip() for key, value in row.items()} for row in reader]
    if extension == "xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise AppError(503, "XLSX_SUPPORT_UNAVAILABLE", "服务未安装 XLSX 解析依赖") from exc
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return [], []
        headers = [str(value or "").strip() for value in rows[0]]
        return headers, [{headers[index]: str(value or "").strip() for index, value in enumerate(row)} for row in rows[1:]]
    raise AppError(415, "USER_IMPORT_TYPE_UNSUPPORTED", "仅支持 CSV 或 XLSX")


@router.post("/user-imports/validate", status_code=201, summary="用户导入预校验")
async def validate_import(
    request: Request,
    file: UploadFile = File(...),
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise AppError(413, "USER_IMPORT_TOO_LARGE", "导入文件不能超过 10MB")
    headers, rows = _parse_import(file.filename or "", content)
    required_headers = {"姓名", "学号"}
    missing_headers = sorted(required_headers.difference(headers))
    if missing_headers:
        raise AppError(
            400,
            "USER_IMPORT_HEADERS_INVALID",
            "导入文件必须包含“姓名”和“学号”表头",
            details={"required": sorted(required_headers), "missing": missing_headers},
        )
    if len(rows) > 10_000:
        raise AppError(400, "USER_IMPORT_TOO_MANY_ROWS", "单次最多导入 10000 行")
    errors, conflicts, normalized, seen_student_numbers = [], [], [], set()
    for index, row in enumerate(rows, 2):
        name = (row.get("姓名") or "").strip()
        student_no = (row.get("学号") or "").strip()
        if not name or not student_no:
            errors.append({"row": index, "message": "姓名和学号不能为空"})
            continue
        if student_no in seen_student_numbers:
            errors.append({"row": index, "message": "文件内学号重复"})
            continue
        seen_student_numbers.add(student_no)
        values = {"row": index, "account": student_no, "name": name, "studentOrStaffNo": student_no, "userType": "student"}
        existing = db.scalar(
            select(User).where(or_(User.account == student_no, User.student_or_staff_no == student_no))
        )
        if existing:
            conflicts.append({"row": index, "account": student_no, "existingUserId": str(existing.id), "type": "student_number_exists"})
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
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    item = db.scalar(select(UserImport).where(UserImport.id == import_id, UserImport.created_by == current.id))
    if not item or as_utc(item.expires_at) <= utcnow():
        raise AppError(404, "USER_IMPORT_NOT_FOUND", "导入记录不存在或已过期")
    if item.status != "validated":
        raise AppError(409, "USER_IMPORT_ALREADY_COMMITTED", "导入记录已提交")
    decisions = {decision.row: decision for decision in payload.decisions}
    normal_role = db.scalar(select(Role).where(Role.code == "normal_user"))
    if normal_role is None:
        raise AppError(500, "NORMAL_ROLE_MISSING", "系统缺少普通用户角色，请先完成初始化")
    task, _ = create_task(db, type="user_import", created_by=current.id, progress_text="正在导入用户")
    update_task(db, task, status="running", stage="importing", progress_percent=10, progress_text="正在导入用户")
    created_count = updated_count = skipped_count = 0
    for row in item.rows_json:
        decision = decisions.get(row["row"])
        if decision and decision.action == "exclude":
            skipped_count += 1
            continue
        values = row
        user = db.scalar(
            select(User).where(
                or_(User.account == values["account"], User.student_or_staff_no == values["studentOrStaffNo"])
            )
        )
        if user:
            skipped_count += 1
            continue
        else:
            user = User(
                account=values["studentOrStaffNo"],
                name=values["name"],
                student_or_staff_no=values["studentOrStaffNo"],
                user_type="student",
                auth_source="local",
                password_hash=hash_password(DEFAULT_INITIAL_PASSWORD),
                must_change_password=True,
                roles=[normal_role],
            )
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
    current: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    task, _ = create_task(db, type="user_export", created_by=current.id, progress_text="等待导出用户")
    record_audit(db, actor=current, module="user", action="export", target_type="task", target_id=task.id, request=request, after=payload.filters)
    db.commit()
    return success(request, task_payload(task, include_events=False), status_code=202)
