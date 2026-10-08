"""Add one-time email login codes.

Revision ID: 20260929_0002
Revises: 20260916_0001
"""
from __future__ import annotations

from alembic import op

revision = "20260929_0002"
down_revision = "20260916_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.email_login_codes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email text NOT NULL,
            code_hash text NOT NULL,
            attempt_count smallint NOT NULL DEFAULT 0,
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT email_login_codes_email_not_blank CHECK (btrim(email) <> ''),
            CONSTRAINT email_login_codes_hash_format CHECK (code_hash ~ '^[0-9a-f]{64}$'),
            CONSTRAINT email_login_codes_attempts_range CHECK (
                attempt_count BETWEEN 0 AND 5
            ),
            CONSTRAINT email_login_codes_expiration_after_creation CHECK (
                expires_at > created_at
            )
        );

        CREATE INDEX email_login_codes_lookup_idx
            ON app.email_login_codes (lower(email), created_at DESC)
            WHERE consumed_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.email_login_codes")
