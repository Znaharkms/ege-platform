"""Student messages and durable administrator email notifications."""
from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "20261006_0008"
down_revision: str | None = "20261005_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    source = Path(__file__).resolve().parents[3] / "database/008_student_dashboard.sql"
    # Execute statements separately for psycopg and asyncpg compatibility.
    for statement in source.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE app.admin_notification_outbox")
    op.execute("DROP TABLE app.admin_notification_settings")
    op.execute("DELETE FROM app.question_error_reports WHERE question_id IS NULL")
    op.execute("ALTER TABLE app.question_error_reports DROP CONSTRAINT "
               "reports_question_context_consistent")
    for column in ("question_id", "question_version", "attempt_question_id"):
        op.execute(f"ALTER TABLE app.question_error_reports ALTER COLUMN {column} SET NOT NULL")
