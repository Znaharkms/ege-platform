"""Add configurable reference books.

Revision ID: 20260929_0003
Revises: 20260929_0002
"""
from __future__ import annotations

from alembic import op

revision = "20260929_0003"
down_revision = "20260929_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.reference_books (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE RESTRICT,
            code text NOT NULL UNIQUE,
            title text NOT NULL,
            kind text NOT NULL DEFAULT 'custom',
            content_type_code text REFERENCES app.content_types(code) ON DELETE RESTRICT,
            source_name text UNIQUE,
            status text NOT NULL DEFAULT 'active',
            created_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            archived_at timestamptz,
            CONSTRAINT reference_books_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
            CONSTRAINT reference_books_title_not_blank CHECK (btrim(title) <> ''),
            CONSTRAINT reference_books_kind_valid CHECK (kind IN ('builtin', 'custom')),
            CONSTRAINT reference_books_status_valid CHECK (status IN ('active', 'archived')),
            CONSTRAINT reference_books_archive_consistent CHECK (
                (status = 'active' AND archived_at IS NULL)
                OR (status = 'archived' AND archived_at IS NOT NULL)
            ),
            CONSTRAINT reference_books_builtin_mapping CHECK (
                kind = 'custom'
                OR (content_type_code IS NOT NULL AND source_name IS NOT NULL)
            )
        );

        CREATE INDEX reference_books_subject_status_idx
            ON app.reference_books (subject_id, status, title);

        CREATE TABLE app.reference_fields (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            reference_book_id uuid NOT NULL
                REFERENCES app.reference_books(id) ON DELETE CASCADE,
            field_key text NOT NULL,
            label text NOT NULL,
            field_type text NOT NULL DEFAULT 'text',
            is_required boolean NOT NULL DEFAULT false,
            sort_order integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT reference_fields_key_format CHECK (
                field_key ~ '^[a-z][a-z0-9_]*$'
            ),
            CONSTRAINT reference_fields_label_not_blank CHECK (btrim(label) <> ''),
            CONSTRAINT reference_fields_type_valid CHECK (
                field_type IN ('text', 'long_text', 'number', 'date', 'boolean')
            ),
            CONSTRAINT reference_fields_book_key_unique
                UNIQUE (reference_book_id, field_key)
        );

        CREATE INDEX reference_fields_book_sort_idx
            ON app.reference_fields (reference_book_id, sort_order, created_at);

        CREATE TABLE app.reference_entries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            reference_book_id uuid NOT NULL
                REFERENCES app.reference_books(id) ON DELETE CASCADE,
            values jsonb NOT NULL DEFAULT '{}'::jsonb,
            is_archived boolean NOT NULL DEFAULT false,
            created_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
            updated_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            archived_at timestamptz,
            CONSTRAINT reference_entries_values_object CHECK (jsonb_typeof(values) = 'object'),
            CONSTRAINT reference_entries_archive_consistent CHECK (
                (is_archived = false AND archived_at IS NULL)
                OR (is_archived = true AND archived_at IS NOT NULL)
            )
        );

        CREATE INDEX reference_entries_book_updated_idx
            ON app.reference_entries (reference_book_id, is_archived, updated_at DESC);
        CREATE INDEX reference_entries_values_gin_idx
            ON app.reference_entries USING gin (values);

        INSERT INTO app.reference_books (
            id, subject_id, code, title, kind, content_type_code, source_name
        )
        SELECT '10000000-0000-4000-8000-000000000001', id,
               'history_dates', 'Исторические даты', 'builtin', 'date', 'history_dates'
        FROM app.subjects WHERE code = 'history';

        INSERT INTO app.reference_books (
            id, subject_id, code, title, kind, content_type_code, source_name
        )
        SELECT '10000000-0000-4000-8000-000000000002', id,
               'history_terms', 'Термины по истории', 'builtin', 'term', 'history_terms'
        FROM app.subjects WHERE code = 'history';

        INSERT INTO app.reference_books (
            id, subject_id, code, title, kind, content_type_code, source_name
        )
        SELECT '10000000-0000-4000-8000-000000000003', id,
               'society_terms', 'Термины по обществознанию', 'builtin', 'term',
               'society_terms'
        FROM app.subjects WHERE code = 'society';

        INSERT INTO app.reference_books (
            id, subject_id, code, title, kind, content_type_code, source_name
        )
        SELECT '10000000-0000-4000-8000-000000000004', id,
               'society_plans', 'Планы по обществознанию', 'builtin', 'plan',
               'society_plans'
        FROM app.subjects WHERE code = 'society';
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.reference_entries")
    op.execute("DROP TABLE IF EXISTS app.reference_fields")
    op.execute("DROP TABLE IF EXISTS app.reference_books")
