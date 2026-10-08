import asyncio
from sqlalchemy import text
from app.db.session import SessionFactory
SCHEMA="""CREATE TABLE IF NOT EXISTS app.search_analytics (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),visitor uuid NOT NULL,
 registered boolean NOT NULL DEFAULT false,is_demo boolean NOT NULL DEFAULT false,
 query text NOT NULL,normalized_query text GENERATED ALWAYS AS(app.normalize_search_text(query)) STORED,
 subject text,category text,kind text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 imported_history_id bigint UNIQUE
)"""
BACKFILL="""INSERT INTO app.search_analytics(visitor,registered,is_demo,query,subject,category,kind,created_at,imported_history_id)
 SELECT h.user_id,true,u.email LIKE 'demo.student%@example.invalid',h.query,s.code,
 coalesce(h.filters->>'type',h.filters->'types'->>0,c.content_type_code),'imported',h.created_at,h.id
 FROM app.search_history h JOIN app.users u ON u.id=h.user_id AND u.role='student'
 LEFT JOIN app.subjects s ON s.id=h.subject_id LEFT JOIN app.content_items c ON c.id=h.selected_content_id
 WHERE coalesce(h.filters->>'analytics','true')='true'
 ON CONFLICT(imported_history_id) DO NOTHING"""
async def upgrade():
 async with SessionFactory.begin() as session:
  await session.execute(text(SCHEMA))
  await session.execute(text('CREATE INDEX IF NOT EXISTS search_analytics_period_idx ON app.search_analytics(created_at,subject,category)'))
  await session.execute(text(BACKFILL))
 print('Независимая статистика поиска подключена; сохранённая история перенесена.')
if __name__=='__main__':asyncio.run(upgrade())
