"""Add history trainer metadata and self-check questions.

Revision ID: 20260930_0005
Revises: 20260930_0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260930_0005"
down_revision: str | None = "20260930_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app.question_types (code, title)
        VALUES ('self_check', 'Развёрнутый ответ с самопроверкой')
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE app.history_question_metadata (
            question_id uuid PRIMARY KEY REFERENCES app.questions(id) ON DELETE CASCADE,
            source_row_id integer NOT NULL UNIQUE,
            task_number smallint NOT NULL,
            period text,
            categories jsonb NOT NULL DEFAULT '[]'::jsonb,
            stimulus_key text,
            review_required boolean NOT NULL DEFAULT false,
            review_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
            imported_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT history_question_task_range CHECK (task_number BETWEEN 1 AND 21),
            CONSTRAINT history_question_categories_array
                CHECK (jsonb_typeof(categories) = 'array'),
            CONSTRAINT history_question_review_reasons_array
                CHECK (jsonb_typeof(review_reasons) = 'array')
        )
        """
    )
    op.execute(
        """
        CREATE INDEX history_question_task_idx
        ON app.history_question_metadata (task_number, period, question_id)
        """
    )
    op.execute(
        """
        CREATE INDEX history_question_categories_idx
        ON app.history_question_metadata USING gin (categories)
        """
    )
    op.execute(
        """
        CREATE INDEX history_question_review_idx
        ON app.history_question_metadata (review_required, task_number, question_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.history_question_metadata")
    op.execute("DELETE FROM app.question_types WHERE code = 'self_check'")
