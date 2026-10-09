"""Create system authentication and user tables.

Revision ID: 20261008_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "20261008_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_departments_parent_id", "departments", ["parent_id"])
    op.create_table(
        "permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("module", sa.String(50), nullable=False),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account", sa.String(100), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("student_or_staff_no", sa.String(50), nullable=True, unique=True),
        sa.Column("user_type", sa.String(30), nullable=False),
        sa.Column("subtitle", sa.String(200), nullable=True),
        sa.Column("department_id", sa.Uuid(), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("avatar_url", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("auth_source", sa.String(20), nullable=False),
        sa.Column("external_subject", sa.String(255), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("auth_source", "external_subject", name="uq_users_auth_source_subject"),
    )
    for name, columns in (
        ("ix_users_account", ["account"]),
        ("ix_users_department_id", ["department_id"]),
        ("ix_users_status", ["status"]),
        ("ix_users_external_subject", ["external_subject"]),
    ):
        op.create_index(name, "users", columns)
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "permission_id", sa.Uuid(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_family", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(50), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, columns in (
        ("ix_auth_sessions_user_id", ["user_id"]),
        ("ix_auth_sessions_token_family", ["token_family"]),
        ("ix_auth_sessions_expires_at", ["expires_at"]),
    ):
        op.create_index(name, "auth_sessions", columns)
    op.create_table(
        "captcha_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("answer_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_captcha_challenges_expires_at", "captcha_challenges", ["expires_at"])
    op.create_table(
        "login_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account", sa.String(100), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, columns in (
        ("ix_login_events_account", ["account"]),
        ("ix_login_events_user_id", ["user_id"]),
        ("ix_login_events_occurred_at", ["occurred_at"]),
    ):
        op.create_index(name, "login_events", columns)
    op.create_table(
        "sso_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("return_to", sa.String(1000), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sso_transactions_expires_at", "sso_transactions", ["expires_at"])


def downgrade() -> None:
    for table in (
        "sso_transactions",
        "login_events",
        "captcha_challenges",
        "auth_sessions",
        "user_roles",
        "role_permissions",
        "users",
        "roles",
        "permissions",
        "departments",
    ):
        op.drop_table(table)
