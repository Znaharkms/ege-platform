"""Create the initial EGE platform schema.

Revision ID: 20260916_0001
Revises: None
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "20260916_0001"
down_revision = None
branch_labels = None
depends_on = None


def _schema_sql() -> str:
    project_root = Path(__file__).resolve().parents[3]
    path = project_root / "database" / "001_initial_schema.sql"
    sql = path.read_text(encoding="utf-8").strip()
    if sql.startswith("BEGIN;"):
        sql = sql.removeprefix("BEGIN;").lstrip()
    if sql.endswith("COMMIT;"):
        sql = sql.removesuffix("COMMIT;").rstrip()
    return sql


def upgrade() -> None:
    # psycopg uses the simple-query protocol for this multi-statement bootstrap.
    op.get_bind().exec_driver_sql(_schema_sql())


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS app CASCADE")

