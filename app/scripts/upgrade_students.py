"""Idempotent addition of private administrator student records."""
import asyncio
from sqlalchemy import text
from app.db.session import SessionFactory

SCHEMA = """CREATE TABLE IF NOT EXISTS app.student_admin_profiles (
 user_id uuid PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
 notes text NOT NULL DEFAULT '', groups text[] NOT NULL DEFAULT '{}',
 subjects text[] NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now(),
 updated_by uuid REFERENCES app.users(id),
 CHECK (subjects <@ ARRAY['history','society']::text[])
)"""

async def upgrade():
    async with SessionFactory.begin() as session:
        await session.execute(text(SCHEMA))
        await session.execute(text("ALTER TABLE app.question_error_reports ADD COLUMN IF NOT EXISTS workflow_status text"))
        await session.execute(text("UPDATE app.question_error_reports SET workflow_status=CASE WHEN status='answered' THEN 'resolved' ELSE 'new' END WHERE workflow_status IS NULL"))
        await session.execute(text("ALTER TABLE app.question_error_reports ALTER COLUMN workflow_status SET DEFAULT 'new', ALTER COLUMN workflow_status SET NOT NULL"))
        await session.execute(text("""DO $$ BEGIN IF NOT EXISTS (
          SELECT 1 FROM pg_constraint WHERE conname='reports_workflow_valid' AND conrelid='app.question_error_reports'::regclass
        ) THEN ALTER TABLE app.question_error_reports ADD CONSTRAINT reports_workflow_valid CHECK(workflow_status IN ('new','in_progress','resolved')); END IF; END $$"""))
    print('Заметки, группы и предметы учеников: база обновлена.')

if __name__ == '__main__':
    asyncio.run(upgrade())
