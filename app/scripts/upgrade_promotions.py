import asyncio
from sqlalchemy import text
from app.db.session import SessionFactory

SCHEMAS=["""CREATE TABLE IF NOT EXISTS app.promotions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),name text NOT NULL,title text NOT NULL,
 body text NOT NULL DEFAULT '',button text NOT NULL DEFAULT 'Подробнее',url text NOT NULL,
 image text NOT NULL DEFAULT '',subject text NOT NULL DEFAULT 'both',placements text[] NOT NULL,
 status text NOT NULL DEFAULT 'draft',priority integer NOT NULL DEFAULT 0,
 starts_at timestamptz,ends_at timestamptz,created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),CHECK(status IN ('draft','active','off')),
 CHECK(subject IN ('history','society','both')),CHECK(ends_at IS NULL OR starts_at IS NULL OR ends_at>starts_at)
)""", """CREATE TABLE IF NOT EXISTS app.promotion_events (
 promotion_id uuid REFERENCES app.promotions(id) ON DELETE CASCADE,visitor uuid NOT NULL,
 placement text NOT NULL,kind text NOT NULL CHECK(kind IN ('view','click')),
 day date NOT NULL DEFAULT (now() AT TIME ZONE 'Europe/Moscow')::date,
 PRIMARY KEY(promotion_id,visitor,placement,kind,day)
)"""]
async def upgrade():
 async with SessionFactory.begin() as session:
  for sql in SCHEMAS:await session.execute(text(sql))
 print('Раздел «Продвижение»: база обновлена.')
if __name__=='__main__':asyncio.run(upgrade())
