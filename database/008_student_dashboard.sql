ALTER TABLE app.question_error_reports ALTER COLUMN question_id DROP NOT NULL;
ALTER TABLE app.question_error_reports ALTER COLUMN question_version DROP NOT NULL;
ALTER TABLE app.question_error_reports ALTER COLUMN attempt_question_id DROP NOT NULL;
ALTER TABLE app.question_error_reports ADD CONSTRAINT reports_question_context_consistent CHECK (
    num_nonnulls(question_id, question_version, attempt_question_id) IN (0, 3)
);
CREATE TABLE app.admin_notification_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    email text NOT NULL
);
CREATE TABLE app.admin_notification_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id uuid NOT NULL UNIQUE REFERENCES app.question_error_reports(id) ON DELETE CASCADE,
    attempt_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    last_error text
);
CREATE INDEX admin_notification_pending_idx ON app.admin_notification_outbox(next_attempt_at)
WHERE sent_at IS NULL;
