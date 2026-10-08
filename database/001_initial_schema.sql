-- Olesya Blok EGE platform
-- PostgreSQL 15+
-- Initial production schema: public reference content, tests, accounts,
-- personal progress, imports, and administrator audit.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS app;
SET search_path = app, public;

-- -----------------------------------------------------------------------------
-- Shared helpers and enums
-- -----------------------------------------------------------------------------

CREATE FUNCTION app.normalize_search_text(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURN trim(
    regexp_replace(
        translate(lower(coalesce(value, '')), 'ё', 'е'),
        '[[:space:]]+',
        ' ',
        'g'
    )
);

CREATE FUNCTION app.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TYPE app.user_role AS ENUM ('student', 'admin');
CREATE TYPE app.account_status AS ENUM ('active', 'blocked', 'deleted');
CREATE TYPE app.identity_provider AS ENUM ('email', 'telegram', 'max');
CREATE TYPE app.client_kind AS ENUM ('web', 'telegram', 'max');
CREATE TYPE app.publication_status AS ENUM ('draft', 'published', 'archived');
CREATE TYPE app.date_precision AS ENUM (
    'day', 'month', 'year', 'range', 'century', 'approximate', 'unknown'
);
CREATE TYPE app.test_mode AS ENUM ('fixed', 'generated');
CREATE TYPE app.attempt_kind AS ENUM ('standard', 'quick', 'review', 'generated');
CREATE TYPE app.attempt_status AS ENUM ('in_progress', 'completed', 'abandoned', 'expired');
CREATE TYPE app.review_status AS ENUM ('pending', 'mastered', 'snoozed');
CREATE TYPE app.import_status AS ENUM ('running', 'completed', 'failed', 'cancelled');

-- -----------------------------------------------------------------------------
-- Users, identities, sessions
-- -----------------------------------------------------------------------------

CREATE TABLE app.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role app.user_role NOT NULL DEFAULT 'student',
    status app.account_status NOT NULL DEFAULT 'active',
    display_name text,
    email text,
    avatar_url text,
    timezone text NOT NULL DEFAULT 'Europe/Moscow',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    deleted_at timestamptz,
    CONSTRAINT users_display_name_not_blank
        CHECK (display_name IS NULL OR btrim(display_name) <> ''),
    CONSTRAINT users_email_not_blank
        CHECK (email IS NULL OR btrim(email) <> ''),
    CONSTRAINT users_deleted_state_consistent
        CHECK (
            (status = 'deleted' AND deleted_at IS NOT NULL)
            OR (status <> 'deleted' AND deleted_at IS NULL)
        )
);

CREATE UNIQUE INDEX users_email_unique_active_idx
    ON app.users (lower(email))
    WHERE email IS NOT NULL AND status <> 'deleted';

CREATE TABLE app.external_identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    provider app.identity_provider NOT NULL,
    provider_user_id text NOT NULL,
    provider_username text,
    provider_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_identities_provider_user_not_blank
        CHECK (btrim(provider_user_id) <> ''),
    CONSTRAINT external_identities_provider_data_object
        CHECK (jsonb_typeof(provider_data) = 'object'),
    CONSTRAINT external_identities_provider_user_unique
        UNIQUE (provider, provider_user_id),
    CONSTRAINT external_identities_user_provider_unique
        UNIQUE (user_id, provider)
);

CREATE INDEX external_identities_user_idx
    ON app.external_identities (user_id);

CREATE TABLE app.sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    refresh_token_hash text NOT NULL UNIQUE,
    client app.client_kind NOT NULL,
    user_agent text,
    ip_address inet,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CONSTRAINT sessions_expiration_after_creation
        CHECK (expires_at > created_at),
    CONSTRAINT sessions_revocation_after_creation
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX sessions_user_active_idx
    ON app.sessions (user_id, expires_at DESC)
    WHERE revoked_at IS NULL;

CREATE TABLE app.anonymous_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    claimed_by_user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
    claimed_at timestamptz,
    CONSTRAINT anonymous_sessions_expiration_after_creation
        CHECK (expires_at > created_at),
    CONSTRAINT anonymous_sessions_claim_consistent
        CHECK (
            (claimed_by_user_id IS NULL AND claimed_at IS NULL)
            OR (claimed_by_user_id IS NOT NULL AND claimed_at IS NOT NULL)
        )
);

CREATE INDEX anonymous_sessions_expiration_idx
    ON app.anonymous_sessions (expires_at);

-- State-changing API operations use an Idempotency-Key. principal_key is a
-- server-derived identity such as user:<uuid>, anon:<uuid> or a short-lived
-- public client fingerprint; raw credentials are never stored here.
CREATE TABLE app.idempotency_keys (
    principal_key text NOT NULL,
    operation_scope text NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash text NOT NULL,
    response_status smallint,
    response_body jsonb,
    resource_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (principal_key, operation_scope, idempotency_key),
    CONSTRAINT idempotency_keys_principal_not_blank CHECK (btrim(principal_key) <> ''),
    CONSTRAINT idempotency_keys_scope_not_blank CHECK (btrim(operation_scope) <> ''),
    CONSTRAINT idempotency_keys_hash_format CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT idempotency_keys_status_range
        CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
    CONSTRAINT idempotency_keys_expiration_after_creation CHECK (expires_at > created_at)
);

CREATE INDEX idempotency_keys_expiration_idx
    ON app.idempotency_keys (expires_at);

-- -----------------------------------------------------------------------------
-- Content taxonomy
-- -----------------------------------------------------------------------------

CREATE TABLE app.subjects (
    id smallint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE,
    title text NOT NULL,
    sort_order smallint NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT subjects_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT subjects_title_not_blank CHECK (btrim(title) <> '')
);

INSERT INTO app.subjects (code, title, sort_order)
VALUES
    ('history', 'История', 10),
    ('society', 'Обществознание', 20);

CREATE TABLE app.sections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE RESTRICT,
    parent_id uuid,
    code text NOT NULL,
    title text NOT NULL,
    description text,
    sort_order integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sections_code_format CHECK (code ~ '^[a-z0-9][a-z0-9_-]*$'),
    CONSTRAINT sections_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT sections_subject_code_unique UNIQUE (subject_id, code),
    CONSTRAINT sections_id_subject_unique UNIQUE (id, subject_id),
    CONSTRAINT sections_parent_same_subject
        FOREIGN KEY (parent_id, subject_id)
        REFERENCES app.sections(id, subject_id)
        ON DELETE RESTRICT,
    CONSTRAINT sections_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE INDEX sections_parent_sort_idx
    ON app.sections (subject_id, parent_id, sort_order, title);

CREATE TABLE app.content_types (
    code text PRIMARY KEY,
    title text NOT NULL,
    sort_order smallint NOT NULL DEFAULT 0,
    CONSTRAINT content_types_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT content_types_title_not_blank CHECK (btrim(title) <> '')
);

INSERT INTO app.content_types (code, title, sort_order)
VALUES
    ('date', 'Дата', 10),
    ('term', 'Термин', 20),
    ('plan', 'План', 30),
    ('person', 'Личность', 40),
    ('event', 'Событие', 50),
    ('map', 'Карта', 60),
    ('theory', 'Теория', 70),
    ('other', 'Другое', 100);

CREATE TABLE app.content_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE RESTRICT,
    section_id uuid,
    content_type_code text NOT NULL REFERENCES app.content_types(code) ON DELETE RESTRICT,
    title text NOT NULL,
    slug text NOT NULL,
    summary text,
    body jsonb NOT NULL DEFAULT '{}'::jsonb,
    status app.publication_status NOT NULL DEFAULT 'draft',
    created_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
    updated_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    archived_at timestamptz,
    CONSTRAINT content_items_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT content_items_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
    CONSTRAINT content_items_body_object CHECK (jsonb_typeof(body) = 'object'),
    CONSTRAINT content_items_subject_slug_unique UNIQUE (subject_id, slug),
    CONSTRAINT content_items_id_subject_unique UNIQUE (id, subject_id),
    CONSTRAINT content_items_section_same_subject
        FOREIGN KEY (section_id, subject_id)
        REFERENCES app.sections(id, subject_id)
        ON DELETE RESTRICT,
    CONSTRAINT content_items_publication_consistent
        CHECK (
            (status = 'draft' AND published_at IS NULL AND archived_at IS NULL)
            OR (status = 'published' AND published_at IS NOT NULL AND archived_at IS NULL)
            OR (status = 'archived' AND archived_at IS NOT NULL)
        )
);

CREATE INDEX content_items_public_catalog_idx
    ON app.content_items (subject_id, content_type_code, section_id, title)
    WHERE status = 'published';

CREATE INDEX content_items_updated_idx
    ON app.content_items (updated_at DESC);

CREATE TABLE app.content_aliases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    alias text NOT NULL,
    normalized_alias text GENERATED ALWAYS AS (app.normalize_search_text(alias)) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT content_aliases_alias_not_blank CHECK (btrim(alias) <> ''),
    CONSTRAINT content_aliases_unique UNIQUE (content_id, normalized_alias)
);

CREATE INDEX content_aliases_trgm_idx
    ON app.content_aliases USING gin (normalized_alias gin_trgm_ops);

CREATE TABLE app.relation_types (
    code text PRIMARY KEY,
    title text NOT NULL,
    is_directional boolean NOT NULL DEFAULT true,
    CONSTRAINT relation_types_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$')
);

INSERT INTO app.relation_types (code, title, is_directional)
VALUES
    ('related', 'Связанный материал', false),
    ('explains', 'Объясняет', true),
    ('prerequisite', 'Предварительная тема', true),
    ('mentions', 'Упоминает', true),
    ('tests', 'Проверяет знание материала', true);

CREATE TABLE app.content_relations (
    source_content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    target_content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    relation_type_code text NOT NULL REFERENCES app.relation_types(code) ON DELETE RESTRICT,
    weight smallint NOT NULL DEFAULT 100,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_content_id, target_content_id, relation_type_code),
    CONSTRAINT content_relations_no_self_relation
        CHECK (source_content_id <> target_content_id),
    CONSTRAINT content_relations_weight_range CHECK (weight BETWEEN 0 AND 1000)
);

CREATE INDEX content_relations_target_idx
    ON app.content_relations (target_content_id, relation_type_code);

CREATE TABLE app.tags (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    slug text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tags_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT tags_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$')
);

CREATE TABLE app.content_tags (
    content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    tag_id uuid NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    PRIMARY KEY (content_id, tag_id)
);

CREATE INDEX content_tags_tag_idx ON app.content_tags (tag_id, content_id);

-- Search text is deliberately denormalized. The importer/admin service rebuilds
-- it after edits so one indexed document includes title, aliases and typed data.
CREATE TABLE app.content_search_documents (
    content_id uuid PRIMARY KEY REFERENCES app.content_items(id) ON DELETE CASCADE,
    title_text text NOT NULL,
    aliases_text text NOT NULL DEFAULT '',
    body_text text NOT NULL DEFAULT '',
    normalized_text text GENERATED ALWAYS AS (
        app.normalize_search_text(title_text || ' ' || aliases_text || ' ' || body_text)
    ) STORED,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('pg_catalog.russian', coalesce(title_text, '')), 'A') ||
        setweight(to_tsvector('pg_catalog.russian', coalesce(aliases_text, '')), 'A') ||
        setweight(to_tsvector('pg_catalog.russian', coalesce(body_text, '')), 'B')
    ) STORED,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX content_search_documents_fts_idx
    ON app.content_search_documents USING gin (search_vector);

CREATE INDEX content_search_documents_trgm_idx
    ON app.content_search_documents USING gin (normalized_text gin_trgm_ops);

-- -----------------------------------------------------------------------------
-- Typed reference content
-- -----------------------------------------------------------------------------

CREATE TABLE app.history_dates (
    content_id uuid PRIMARY KEY REFERENCES app.content_items(id) ON DELETE CASCADE,
    date_label text NOT NULL,
    date_start date,
    date_end date,
    precision app.date_precision NOT NULL DEFAULT 'unknown',
    event_text text NOT NULL,
    CONSTRAINT history_dates_label_not_blank CHECK (btrim(date_label) <> ''),
    CONSTRAINT history_dates_event_not_blank CHECK (btrim(event_text) <> ''),
    CONSTRAINT history_dates_range_order
        CHECK (date_start IS NULL OR date_end IS NULL OR date_end >= date_start)
);

CREATE INDEX history_dates_range_idx
    ON app.history_dates (date_start, date_end);

CREATE TABLE app.terms (
    content_id uuid PRIMARY KEY REFERENCES app.content_items(id) ON DELETE CASCADE,
    definition text NOT NULL,
    CONSTRAINT terms_definition_not_blank CHECK (btrim(definition) <> '')
);

CREATE TABLE app.term_features (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    term_content_id uuid NOT NULL REFERENCES app.terms(content_id) ON DELETE CASCADE,
    feature_type text NOT NULL DEFAULT 'feature',
    feature_text text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT term_features_type_not_blank CHECK (btrim(feature_type) <> ''),
    CONSTRAINT term_features_text_not_blank CHECK (btrim(feature_text) <> ''),
    CONSTRAINT term_features_unique
        UNIQUE (term_content_id, feature_type, sort_order)
);

CREATE INDEX term_features_term_sort_idx
    ON app.term_features (term_content_id, feature_type, sort_order);

CREATE TABLE app.plans (
    content_id uuid PRIMARY KEY REFERENCES app.content_items(id) ON DELETE CASCADE,
    introduction text,
    conclusion text
);

CREATE TABLE app.plan_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_content_id uuid NOT NULL REFERENCES app.plans(content_id) ON DELETE CASCADE,
    parent_id uuid,
    item_text text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plan_items_text_not_blank CHECK (btrim(item_text) <> ''),
    CONSTRAINT plan_items_id_plan_unique UNIQUE (id, plan_content_id),
    CONSTRAINT plan_items_parent_same_plan
        FOREIGN KEY (parent_id, plan_content_id)
        REFERENCES app.plan_items(id, plan_content_id)
        ON DELETE CASCADE,
    CONSTRAINT plan_items_not_own_parent CHECK (parent_id IS NULL OR parent_id <> id),
    CONSTRAINT plan_items_sibling_position_unique
        UNIQUE NULLS NOT DISTINCT (plan_content_id, parent_id, sort_order)
);

CREATE INDEX plan_items_tree_idx
    ON app.plan_items (plan_content_id, parent_id, sort_order);

-- -----------------------------------------------------------------------------
-- Media
-- -----------------------------------------------------------------------------

CREATE TABLE app.media_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_key text NOT NULL UNIQUE,
    original_filename text NOT NULL,
    mime_type text NOT NULL,
    byte_size bigint NOT NULL,
    width integer,
    height integer,
    alt_text text,
    checksum_sha256 text NOT NULL,
    uploaded_by uuid REFERENCES app.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT media_assets_object_key_not_blank CHECK (btrim(object_key) <> ''),
    CONSTRAINT media_assets_filename_not_blank CHECK (btrim(original_filename) <> ''),
    CONSTRAINT media_assets_positive_size CHECK (byte_size > 0),
    CONSTRAINT media_assets_dimensions_positive
        CHECK ((width IS NULL OR width > 0) AND (height IS NULL OR height > 0)),
    CONSTRAINT media_assets_checksum_format CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE app.content_assets (
    content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    asset_id uuid NOT NULL REFERENCES app.media_assets(id) ON DELETE RESTRICT,
    role text NOT NULL DEFAULT 'attachment',
    sort_order integer NOT NULL DEFAULT 0,
    PRIMARY KEY (content_id, asset_id, role)
);

-- -----------------------------------------------------------------------------
-- Question bank and tests
-- -----------------------------------------------------------------------------

CREATE TABLE app.question_types (
    code text PRIMARY KEY,
    title text NOT NULL,
    answer_schema_version smallint NOT NULL DEFAULT 1,
    is_active boolean NOT NULL DEFAULT true,
    CONSTRAINT question_types_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$')
);

INSERT INTO app.question_types (code, title)
VALUES
    ('single_choice', 'Один правильный ответ'),
    ('multiple_choice', 'Несколько правильных ответов'),
    ('text', 'Текстовый ответ'),
    ('matching', 'Сопоставление'),
    ('ordering', 'Последовательность'),
    ('map', 'Работа с картой'),
    ('graph', 'Работа с графиком'),
    ('self_check', 'Развёрнутый ответ с самопроверкой');

CREATE TABLE app.questions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE RESTRICT,
    section_id uuid,
    question_type_code text NOT NULL REFERENCES app.question_types(code) ON DELETE RESTRICT,
    status app.publication_status NOT NULL DEFAULT 'draft',
    current_version integer NOT NULL DEFAULT 1,
    created_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    updated_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    archived_at timestamptz,
    CONSTRAINT questions_version_positive CHECK (current_version > 0),
    CONSTRAINT questions_id_subject_unique UNIQUE (id, subject_id),
    CONSTRAINT questions_section_same_subject
        FOREIGN KEY (section_id, subject_id)
        REFERENCES app.sections(id, subject_id)
        ON DELETE RESTRICT,
    CONSTRAINT questions_publication_consistent
        CHECK (
            (status = 'draft' AND published_at IS NULL AND archived_at IS NULL)
            OR (status = 'published' AND published_at IS NOT NULL AND archived_at IS NULL)
            OR (status = 'archived' AND archived_at IS NOT NULL)
        )
);

CREATE INDEX questions_public_bank_idx
    ON app.questions (subject_id, section_id, question_type_code)
    WHERE status = 'published';

CREATE TABLE app.question_versions (
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE CASCADE,
    version integer NOT NULL,
    prompt jsonb NOT NULL,
    answer_spec jsonb NOT NULL,
    explanation jsonb NOT NULL DEFAULT '{}'::jsonb,
    default_points numeric(8,2) NOT NULL DEFAULT 1,
    created_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (question_id, version),
    CONSTRAINT question_versions_version_positive CHECK (version > 0),
    CONSTRAINT question_versions_prompt_object CHECK (jsonb_typeof(prompt) = 'object'),
    CONSTRAINT question_versions_answer_object CHECK (jsonb_typeof(answer_spec) = 'object'),
    CONSTRAINT question_versions_explanation_object
        CHECK (jsonb_typeof(explanation) = 'object'),
    CONSTRAINT question_versions_points_positive CHECK (default_points > 0)
);

-- The pointer on questions must always resolve to a real immutable version.
-- It is deferred so the question and its first version can be inserted in one
-- transaction.
ALTER TABLE app.questions
    ADD CONSTRAINT questions_current_version_reference
    FOREIGN KEY (id, current_version)
    REFERENCES app.question_versions(question_id, version)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE app.question_content_links (
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE CASCADE,
    content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    link_role text NOT NULL DEFAULT 'tests',
    weight smallint NOT NULL DEFAULT 100,
    PRIMARY KEY (question_id, content_id, link_role),
    CONSTRAINT question_content_links_weight_range CHECK (weight BETWEEN 0 AND 1000)
);

CREATE INDEX question_content_links_content_idx
    ON app.question_content_links (content_id, question_id);

CREATE TABLE app.question_tags (
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE CASCADE,
    tag_id uuid NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    PRIMARY KEY (question_id, tag_id)
);

CREATE INDEX question_tags_tag_idx ON app.question_tags (tag_id, question_id);

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
    CONSTRAINT history_question_categories_array CHECK (jsonb_typeof(categories) = 'array'),
    CONSTRAINT history_question_review_reasons_array
        CHECK (jsonb_typeof(review_reasons) = 'array')
);

CREATE INDEX history_question_task_idx
    ON app.history_question_metadata (task_number, period, question_id);
CREATE INDEX history_question_categories_idx
    ON app.history_question_metadata USING gin (categories);
CREATE INDEX history_question_review_idx
    ON app.history_question_metadata (review_required, task_number, question_id);

CREATE TABLE app.tests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE RESTRICT,
    section_id uuid,
    title text NOT NULL,
    slug text NOT NULL,
    description text,
    mode app.test_mode NOT NULL DEFAULT 'fixed',
    blueprint jsonb NOT NULL DEFAULT '{}'::jsonb,
    time_limit_seconds integer,
    status app.publication_status NOT NULL DEFAULT 'draft',
    created_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    updated_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    archived_at timestamptz,
    CONSTRAINT tests_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT tests_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
    CONSTRAINT tests_subject_slug_unique UNIQUE (subject_id, slug),
    CONSTRAINT tests_blueprint_object CHECK (jsonb_typeof(blueprint) = 'object'),
    CONSTRAINT tests_time_limit_positive
        CHECK (time_limit_seconds IS NULL OR time_limit_seconds > 0),
    CONSTRAINT tests_section_same_subject
        FOREIGN KEY (section_id, subject_id)
        REFERENCES app.sections(id, subject_id)
        ON DELETE RESTRICT,
    CONSTRAINT tests_publication_consistent
        CHECK (
            (status = 'draft' AND published_at IS NULL AND archived_at IS NULL)
            OR (status = 'published' AND published_at IS NOT NULL AND archived_at IS NULL)
            OR (status = 'archived' AND archived_at IS NOT NULL)
        )
);

CREATE INDEX tests_public_catalog_idx
    ON app.tests (subject_id, section_id, title)
    WHERE status = 'published';

CREATE TABLE app.test_questions (
    test_id uuid NOT NULL REFERENCES app.tests(id) ON DELETE CASCADE,
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE RESTRICT,
    position integer NOT NULL,
    points_override numeric(8,2),
    is_required boolean NOT NULL DEFAULT true,
    PRIMARY KEY (test_id, question_id),
    CONSTRAINT test_questions_position_positive CHECK (position > 0),
    CONSTRAINT test_questions_points_positive
        CHECK (points_override IS NULL OR points_override > 0),
    CONSTRAINT test_questions_position_unique UNIQUE (test_id, position)
);

-- -----------------------------------------------------------------------------
-- Attempts and submitted answers
-- -----------------------------------------------------------------------------

CREATE TABLE app.test_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES app.users(id) ON DELETE RESTRICT,
    anonymous_session_id uuid REFERENCES app.anonymous_sessions(id) ON DELETE RESTRICT,
    test_id uuid REFERENCES app.tests(id) ON DELETE SET NULL,
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE RESTRICT,
    section_id uuid,
    kind app.attempt_kind NOT NULL DEFAULT 'standard',
    status app.attempt_status NOT NULL DEFAULT 'in_progress',
    test_title_snapshot text NOT NULL,
    score numeric(10,2),
    max_score numeric(10,2),
    correct_count integer NOT NULL DEFAULT 0,
    question_count integer NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL DEFAULT now(),
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    expires_at timestamptz,
    CONSTRAINT test_attempts_exactly_one_owner
        CHECK (num_nonnulls(user_id, anonymous_session_id) = 1),
    CONSTRAINT test_attempts_title_not_blank CHECK (btrim(test_title_snapshot) <> ''),
    CONSTRAINT test_attempts_scores_nonnegative
        CHECK (
            (score IS NULL OR score >= 0)
            AND (max_score IS NULL OR max_score >= 0)
            AND (score IS NULL OR max_score IS NULL OR score <= max_score)
        ),
    CONSTRAINT test_attempts_counts_nonnegative
        CHECK (correct_count >= 0 AND question_count >= 0 AND correct_count <= question_count),
    CONSTRAINT test_attempts_completion_consistent
        CHECK (
            (status = 'completed' AND completed_at IS NOT NULL)
            OR (status <> 'completed' AND completed_at IS NULL)
        ),
    CONSTRAINT test_attempts_section_same_subject
        FOREIGN KEY (section_id, subject_id)
        REFERENCES app.sections(id, subject_id)
        ON DELETE RESTRICT
);

CREATE INDEX test_attempts_user_history_idx
    ON app.test_attempts (user_id, started_at DESC)
    WHERE user_id IS NOT NULL;

CREATE INDEX test_attempts_anonymous_idx
    ON app.test_attempts (anonymous_session_id, started_at DESC)
    WHERE anonymous_session_id IS NOT NULL;

CREATE INDEX test_attempts_active_expiration_idx
    ON app.test_attempts (expires_at)
    WHERE status = 'in_progress' AND expires_at IS NOT NULL;

CREATE TABLE app.attempt_questions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL REFERENCES app.test_attempts(id) ON DELETE CASCADE,
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE RESTRICT,
    question_version integer NOT NULL,
    position integer NOT NULL,
    points_possible numeric(8,2) NOT NULL,
    randomization jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT attempt_questions_version_reference
        FOREIGN KEY (question_id, question_version)
        REFERENCES app.question_versions(question_id, version)
        ON DELETE RESTRICT,
    CONSTRAINT attempt_questions_position_positive CHECK (position > 0),
    CONSTRAINT attempt_questions_points_positive CHECK (points_possible > 0),
    CONSTRAINT attempt_questions_randomization_object
        CHECK (jsonb_typeof(randomization) = 'object'),
    CONSTRAINT attempt_questions_position_unique UNIQUE (attempt_id, position),
    CONSTRAINT attempt_questions_question_unique UNIQUE (attempt_id, question_id)
);

CREATE TABLE app.attempt_answers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_question_id uuid NOT NULL UNIQUE
        REFERENCES app.attempt_questions(id) ON DELETE CASCADE,
    response jsonb NOT NULL,
    is_correct boolean,
    points_awarded numeric(8,2),
    feedback jsonb NOT NULL DEFAULT '{}'::jsonb,
    duration_seconds integer,
    answered_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT attempt_answers_points_nonnegative
        CHECK (points_awarded IS NULL OR points_awarded >= 0),
    CONSTRAINT attempt_answers_duration_nonnegative
        CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    CONSTRAINT attempt_answers_feedback_object
        CHECK (jsonb_typeof(feedback) = 'object')
);

CREATE TABLE app.question_error_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE RESTRICT,
    question_version integer NOT NULL,
    attempt_question_id uuid NOT NULL UNIQUE
        REFERENCES app.attempt_questions(id) ON DELETE RESTRICT,
    user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
    anonymous_session_id uuid REFERENCES app.anonymous_sessions(id) ON DELETE SET NULL,
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
    CONSTRAINT question_error_reports_message_not_blank CHECK (btrim(message) <> ''),
    CONSTRAINT question_error_reports_status_valid CHECK (status IN ('open', 'answered')),
    CONSTRAINT question_error_reports_reply_consistent CHECK (
        (status = 'open' AND admin_reply IS NULL AND replied_at IS NULL)
        OR
        (status = 'answered' AND btrim(admin_reply) <> '' AND replied_at IS NOT NULL)
    )
);

CREATE INDEX question_error_reports_admin_idx
    ON app.question_error_reports (status, created_at DESC);
CREATE INDEX question_error_reports_user_idx
    ON app.question_error_reports (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;
CREATE INDEX question_error_reports_anonymous_idx
    ON app.question_error_reports (anonymous_session_id, created_at DESC)
    WHERE anonymous_session_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Registered-user personalization
-- -----------------------------------------------------------------------------

CREATE TABLE app.search_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    query text NOT NULL,
    normalized_query text GENERATED ALWAYS AS (app.normalize_search_text(query)) STORED,
    subject_id smallint REFERENCES app.subjects(id) ON DELETE SET NULL,
    filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    selected_content_id uuid REFERENCES app.content_items(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT search_history_query_not_blank CHECK (btrim(query) <> ''),
    CONSTRAINT search_history_filters_object CHECK (jsonb_typeof(filters) = 'object')
);

CREATE INDEX search_history_user_recent_idx
    ON app.search_history (user_id, created_at DESC);

CREATE INDEX search_history_user_query_idx
    ON app.search_history (user_id, normalized_query, created_at DESC);

CREATE TABLE app.favorites (
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    content_id uuid NOT NULL REFERENCES app.content_items(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, content_id)
);

CREATE INDEX favorites_user_recent_idx
    ON app.favorites (user_id, created_at DESC);

CREATE TABLE app.user_question_stats (
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE CASCADE,
    attempt_count integer NOT NULL DEFAULT 0,
    correct_count integer NOT NULL DEFAULT 0,
    wrong_count integer NOT NULL DEFAULT 0,
    mastery_score numeric(5,4) NOT NULL DEFAULT 0,
    first_answered_at timestamptz,
    last_answered_at timestamptz,
    last_was_correct boolean,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, question_id),
    CONSTRAINT user_question_stats_counts_valid
        CHECK (
            attempt_count >= 0
            AND correct_count >= 0
            AND wrong_count >= 0
            AND correct_count + wrong_count <= attempt_count
        ),
    CONSTRAINT user_question_stats_mastery_range
        CHECK (mastery_score BETWEEN 0 AND 1)
);

CREATE INDEX user_question_stats_weak_idx
    ON app.user_question_stats (user_id, mastery_score, wrong_count DESC);

CREATE TABLE app.user_section_progress (
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    section_id uuid NOT NULL REFERENCES app.sections(id) ON DELETE CASCADE,
    answered_count integer NOT NULL DEFAULT 0,
    correct_count integer NOT NULL DEFAULT 0,
    completed_tests integer NOT NULL DEFAULT 0,
    mastery_score numeric(5,4) NOT NULL DEFAULT 0,
    last_activity_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, section_id),
    CONSTRAINT user_section_progress_counts_valid
        CHECK (
            answered_count >= 0
            AND correct_count >= 0
            AND completed_tests >= 0
            AND correct_count <= answered_count
        ),
    CONSTRAINT user_section_progress_mastery_range
        CHECK (mastery_score BETWEEN 0 AND 1)
);

CREATE INDEX user_section_progress_user_activity_idx
    ON app.user_section_progress (user_id, last_activity_at DESC);

CREATE TABLE app.user_subject_progress (
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE CASCADE,
    answered_count integer NOT NULL DEFAULT 0,
    correct_count integer NOT NULL DEFAULT 0,
    completed_tests integer NOT NULL DEFAULT 0,
    mastery_score numeric(5,4) NOT NULL DEFAULT 0,
    last_activity_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, subject_id),
    CONSTRAINT user_subject_progress_counts_valid
        CHECK (
            answered_count >= 0
            AND correct_count >= 0
            AND completed_tests >= 0
            AND correct_count <= answered_count
        ),
    CONSTRAINT user_subject_progress_mastery_range
        CHECK (mastery_score BETWEEN 0 AND 1)
);

CREATE TABLE app.review_queue (
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    question_id uuid NOT NULL REFERENCES app.questions(id) ON DELETE CASCADE,
    status app.review_status NOT NULL DEFAULT 'pending',
    error_count integer NOT NULL DEFAULT 1,
    successful_reviews integer NOT NULL DEFAULT 0,
    last_result_correct boolean,
    last_reviewed_at timestamptz,
    next_review_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, question_id),
    CONSTRAINT review_queue_counts_valid
        CHECK (error_count > 0 AND successful_reviews >= 0)
);

CREATE INDEX review_queue_due_idx
    ON app.review_queue (user_id, next_review_at)
    WHERE status = 'pending';

CREATE TABLE app.user_daily_activity (
    user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    subject_id smallint NOT NULL REFERENCES app.subjects(id) ON DELETE CASCADE,
    activity_date date NOT NULL,
    questions_answered integer NOT NULL DEFAULT 0,
    correct_answers integer NOT NULL DEFAULT 0,
    tests_completed integer NOT NULL DEFAULT 0,
    active_seconds integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, subject_id, activity_date),
    CONSTRAINT user_daily_activity_values_valid
        CHECK (
            questions_answered >= 0
            AND correct_answers >= 0
            AND tests_completed >= 0
            AND active_seconds >= 0
            AND correct_answers <= questions_answered
        )
);

-- -----------------------------------------------------------------------------
-- SQLite imports and administrator audit
-- -----------------------------------------------------------------------------

CREATE TABLE app.import_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_name text NOT NULL,
    source_filename text NOT NULL,
    source_checksum_sha256 text NOT NULL,
    dry_run boolean NOT NULL DEFAULT true,
    status app.import_status NOT NULL DEFAULT 'running',
    started_by uuid NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    inserted_count integer NOT NULL DEFAULT 0,
    updated_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    error_report jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT import_runs_source_name_not_blank CHECK (btrim(source_name) <> ''),
    CONSTRAINT import_runs_source_filename_not_blank CHECK (btrim(source_filename) <> ''),
    CONSTRAINT import_runs_checksum_format
        CHECK (source_checksum_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT import_runs_counts_nonnegative
        CHECK (
            inserted_count >= 0
            AND updated_count >= 0
            AND skipped_count >= 0
            AND failed_count >= 0
        ),
    CONSTRAINT import_runs_error_report_array
        CHECK (jsonb_typeof(error_report) = 'array'),
    CONSTRAINT import_runs_completion_consistent
        CHECK (
            (status = 'running' AND completed_at IS NULL)
            OR (status <> 'running' AND completed_at IS NOT NULL)
        )
);

CREATE INDEX import_runs_source_recent_idx
    ON app.import_runs (source_name, started_at DESC);

CREATE TABLE app.source_mappings (
    source_name text NOT NULL,
    source_table text NOT NULL,
    source_record_id text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    source_hash text NOT NULL,
    last_import_run_id uuid NOT NULL REFERENCES app.import_runs(id) ON DELETE RESTRICT,
    first_imported_at timestamptz NOT NULL DEFAULT now(),
    last_imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_name, source_table, source_record_id),
    CONSTRAINT source_mappings_values_not_blank
        CHECK (
            btrim(source_name) <> ''
            AND btrim(source_table) <> ''
            AND btrim(source_record_id) <> ''
            AND btrim(entity_type) <> ''
            AND btrim(source_hash) <> ''
        )
);

CREATE INDEX source_mappings_entity_idx
    ON app.source_mappings (entity_type, entity_id);

CREATE TABLE app.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
    action text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid,
    old_data jsonb,
    new_data jsonb,
    request_id uuid,
    ip_address inet,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audit_log_action_not_blank CHECK (btrim(action) <> ''),
    CONSTRAINT audit_log_entity_type_not_blank CHECK (btrim(entity_type) <> ''),
    CONSTRAINT audit_log_old_data_object
        CHECK (old_data IS NULL OR jsonb_typeof(old_data) = 'object'),
    CONSTRAINT audit_log_new_data_object
        CHECK (new_data IS NULL OR jsonb_typeof(new_data) = 'object')
);

CREATE INDEX audit_log_entity_idx
    ON app.audit_log (entity_type, entity_id, created_at DESC);

CREATE INDEX audit_log_actor_idx
    ON app.audit_log (actor_user_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- updated_at triggers
-- -----------------------------------------------------------------------------

CREATE TRIGGER users_set_updated_at
BEFORE UPDATE ON app.users
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER external_identities_set_updated_at
BEFORE UPDATE ON app.external_identities
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER subjects_set_updated_at
BEFORE UPDATE ON app.subjects
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER sections_set_updated_at
BEFORE UPDATE ON app.sections
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER content_items_set_updated_at
BEFORE UPDATE ON app.content_items
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER term_features_set_updated_at
BEFORE UPDATE ON app.term_features
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER plan_items_set_updated_at
BEFORE UPDATE ON app.plan_items
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER questions_set_updated_at
BEFORE UPDATE ON app.questions
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER tests_set_updated_at
BEFORE UPDATE ON app.tests
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER user_question_stats_set_updated_at
BEFORE UPDATE ON app.user_question_stats
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER user_section_progress_set_updated_at
BEFORE UPDATE ON app.user_section_progress
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER user_subject_progress_set_updated_at
BEFORE UPDATE ON app.user_subject_progress
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER review_queue_set_updated_at
BEFORE UPDATE ON app.review_queue
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

CREATE TRIGGER user_daily_activity_set_updated_at
BEFORE UPDATE ON app.user_daily_activity
FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- Documentation comments for security-sensitive fields.
COMMENT ON COLUMN app.question_versions.answer_spec IS
    'Never return to a client before its answer has been submitted and graded.';
COMMENT ON TABLE app.content_search_documents IS
    'Rebuilt by the admin/import service after content or alias changes.';
COMMENT ON TABLE app.audit_log IS
    'Append-only application audit. UPDATE and DELETE must not be exposed by the API.';
COMMENT ON TABLE app.anonymous_sessions IS
    'Short-lived technical sessions for anonymous test attempts; not user accounts.';

COMMIT;
