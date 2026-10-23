"""seed the university's academic departments

Revision ID: 20261023_0009
Revises: 20261016_0008
Create Date: 2026-10-23
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "20261023_0009"
down_revision = "20261016_0008"
branch_labels = None
depends_on = None


ACADEMIC_DEPARTMENTS = (
    ("MARXISM", "马克思主义学院"),
    ("MECHANICAL_ENGINEERING", "机械工程学院"),
    ("MATERIALS_ENGINEERING", "材料工程学院"),
    ("ELECTRICAL_ENGINEERING", "电气工程学院"),
    ("CIVIL_ENGINEERING", "建筑工程学院"),
    ("TRANSPORTATION_ENGINEERING", "交通工程学院"),
    ("SOFTWARE_ENGINEERING", "软件工程学院"),
    ("INTELLIGENT_MANUFACTURING_INDUSTRY", "智能制造产业学院"),
    ("ECONOMICS_MANAGEMENT", "经济管理学院"),
    ("TOURISM_MANAGEMENT", "旅游管理学院"),
    ("ARTS", "艺术学院"),
    ("EDUCATION", "教育学院"),
    ("FOREIGN_LANGUAGES", "外国语学院"),
    ("SPORTS_HEALTH", "体育与健康学院"),
)


departments = sa.table(
    "departments",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.String()),
    sa.column("code", sa.String()),
    sa.column("status", sa.String()),
)


def seed_academic_departments(connection: sa.Connection) -> None:
    for code, name in ACADEMIC_DEPARTMENTS:
        department_id = connection.scalar(
            sa.select(departments.c.id).where(departments.c.code == code)
        )
        if department_id is None:
            department_id = connection.scalar(
                sa.select(departments.c.id).where(departments.c.name == name)
            )
        if department_id is None:
            connection.execute(
                departments.insert().values(
                    id=uuid.uuid4(),
                    name=name,
                    code=code,
                    status="active",
                )
            )
        else:
            connection.execute(
                departments.update()
                .where(departments.c.id == department_id)
                .values(name=name, code=code, status="active")
            )


def upgrade() -> None:
    seed_academic_departments(op.get_bind())


def downgrade() -> None:
    # Departments may already be assigned to users or knowledge bases, so keep them.
    pass
