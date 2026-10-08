"""Add student reports about errors in questions.

Revision ID: 20261005_0007
Revises: 20260930_0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261005_0007"
down_revision: str | None = "20260930_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.question_error_reports (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE RESTRICT,
            question_version integer NOT NULL,
            attempt_question_id uuid NOT NULL UNIQUE
                REFERENCES app.attempt_questions(id) ON DELETE RESTRICT,
            user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
            anonymous_session_id uuid
                REFERENCES app.anonymous_sessions(id) ON DELETE SET NULL,
            message text NOT NULL,
            status text NOT NULL DEFAULT 'open',
            admin_reply text,
            replied_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            replied_at timestamptz,
            student_viewed_at timestamptz,
            CONSTRAINT question_error_reports_version_fk
                FOREIGN KEY (question_id, question_version)
                REFERENCES app.question_versions(question_id, version)
                ON DELETE RESTRICT,
            CONSTRAINT question_error_reports_message_not_blank
                CHECK (btrim(message) <> ''),
            CONSTRAINT question_error_reports_status_valid
                CHECK (status IN ('open', 'answered')),
            CONSTRAINT question_error_reports_reply_consistent
                CHECK (
                    (status = 'open' AND admin_reply IS NULL AND replied_at IS NULL)
                    OR
                    (status = 'answered' AND btrim(admin_reply) <> '' AND replied_at IS NOT NULL)
                )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX question_error_reports_admin_idx
        ON app.question_error_reports (status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX question_error_reports_user_idx
        ON app.question_error_reports (user_id, created_at DESC)
        WHERE user_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX question_error_reports_anonymous_idx
        ON app.question_error_reports (anonymous_session_id, created_at DESC)
        WHERE anonymous_session_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.question_error_reports")
