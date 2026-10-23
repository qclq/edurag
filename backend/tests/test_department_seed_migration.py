import importlib.util
from pathlib import Path
import sys
import types
import uuid

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "20261023_0009_seed_academic_departments.py"
)
EXPECTED_DEPARTMENTS = {
    "马克思主义学院",
    "机械工程学院",
    "材料工程学院",
    "电气工程学院",
    "建筑工程学院",
    "交通工程学院",
    "软件工程学院",
    "智能制造产业学院",
    "经济管理学院",
    "旅游管理学院",
    "艺术学院",
    "教育学院",
    "外国语学院",
    "体育与健康学院",
}


def _load_migration():
    original_alembic = sys.modules.get("alembic")
    needs_stub = original_alembic is None or not hasattr(original_alembic, "op")
    if needs_stub:
        alembic_stub = types.ModuleType("alembic")
        alembic_stub.op = None
        sys.modules["alembic"] = alembic_stub
    try:
        spec = importlib.util.spec_from_file_location("seed_academic_departments", MIGRATION_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        if needs_stub and original_alembic is None:
            sys.modules.pop("alembic", None)
        elif needs_stub:
            sys.modules["alembic"] = original_alembic


def test_department_seed_is_complete_and_idempotent():
    migration = _load_migration()
    metadata = sa.MetaData()
    departments = sa.Table(
        "departments",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)

    existing_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            departments.insert().values(
                id=existing_id,
                name="机械工程学院",
                code="OLD_MECHANICAL_CODE",
                status="disabled",
            )
        )
        migration.seed_academic_departments(connection)
        migration.seed_academic_departments(connection)
        rows = connection.execute(sa.select(departments)).mappings().all()

    assert len(rows) == 14
    assert {row["name"] for row in rows} == EXPECTED_DEPARTMENTS
    assert all(row["status"] == "active" for row in rows)
    mechanical = next(row for row in rows if row["name"] == "机械工程学院")
    assert mechanical["id"] == existing_id
    assert mechanical["code"] == "MECHANICAL_ENGINEERING"
