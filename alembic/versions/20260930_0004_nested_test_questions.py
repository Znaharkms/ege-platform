"""Remove question difficulty for the nested test editor.

Revision ID: 20260930_0004
Revises: 20260929_0003
"""
from __future__ import annotations

from alembic import op

revision = "20260930_0004"
down_revision = "20260929_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS app.questions_public_bank_idx;
        ALTER TABLE app.questions
            DROP CONSTRAINT IF EXISTS questions_difficulty_range,
            DROP COLUMN IF EXISTS difficulty;
        CREATE INDEX questions_public_bank_idx
            ON app.questions (subject_id, section_id, question_type_code)
            WHERE status = 'published';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS app.questions_public_bank_idx;
        ALTER TABLE app.questions
            ADD COLUMN difficulty smallint NOT NULL DEFAULT 3,
            ADD CONSTRAINT questions_difficulty_range
                CHECK (difficulty BETWEEN 1 AND 5);
        CREATE INDEX questions_public_bank_idx
            ON app.questions (subject_id, section_id, question_type_code, difficulty)
            WHERE status = 'published';
        """
    )
