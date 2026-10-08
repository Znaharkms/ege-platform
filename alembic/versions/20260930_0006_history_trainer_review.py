"""Add review workflow to imported history questions.

Revision ID: 20260930_0006
Revises: 20260930_0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260930_0006"
down_revision: str | None = "20260930_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app.history_question_metadata
        ADD COLUMN IF NOT EXISTS review_required boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS review_reasons jsonb NOT NULL DEFAULT '[]'::jsonb
        """
    )
    op.execute(
        """
        ALTER TABLE app.history_question_metadata
        DROP CONSTRAINT IF EXISTS history_question_review_reasons_array
        """
    )
    op.execute(
        """
        ALTER TABLE app.history_question_metadata
        ADD CONSTRAINT history_question_review_reasons_array
        CHECK (jsonb_typeof(review_reasons) = 'array')
        """
    )
    op.execute(
        """
        UPDATE app.history_question_metadata AS metadata
        SET review_required = true,
            review_reasons = '["Требует проверки после импорта"]'::jsonb
        FROM app.questions AS question
        WHERE question.id = metadata.question_id
          AND question.status = 'draft'
          AND metadata.review_required = false
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS history_question_review_idx
        ON app.history_question_metadata (review_required, task_number, question_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS app.history_question_review_idx")
    op.execute(
        """
        ALTER TABLE app.history_question_metadata
        DROP CONSTRAINT IF EXISTS history_question_review_reasons_array,
        DROP COLUMN IF EXISTS review_reasons,
        DROP COLUMN IF EXISTS review_required
        """
    )
