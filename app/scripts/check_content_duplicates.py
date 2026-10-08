"""Find exact published content duplicates; optionally archive copies."""
import argparse,json
from collections import defaultdict
from sqlalchemy import text
from app.core.config import get_settings
from app.importing.sqlite_content import create_sync_engine


def main():
    p=argparse.ArgumentParser(description='Проверка точных дублей материалов')
    p.add_argument('--apply',action='store_true',help='Архивировать точные копии, сохранив избранное')
    args=p.parse_args();engine=create_sync_engine(get_settings().sync_database_url)
    try:
        with engine.begin() as c:
            rows=c.execute(text("""SELECT ci.id,ci.subject_id,ci.section_id,ci.content_type_code,ci.title,ci.summary,ci.body,
              (SELECT definition FROM app.terms WHERE content_id=ci.id) AS definition,
              (SELECT jsonb_agg(jsonb_build_array(feature_type,feature_text,sort_order) ORDER BY sort_order,id) FROM app.term_features WHERE term_content_id=ci.id) AS features,
              (SELECT to_jsonb(d)-'content_id' FROM app.history_dates d WHERE content_id=ci.id) AS date_data,
              (SELECT to_jsonb(p)-'content_id' FROM app.plans p WHERE content_id=ci.id) AS plan_data,
              (SELECT jsonb_agg(jsonb_build_array(pi.item_text,pi.sort_order,parent.item_text,parent.sort_order) ORDER BY pi.sort_order,pi.item_text) FROM app.plan_items pi LEFT JOIN app.plan_items parent ON parent.id=pi.parent_id WHERE pi.plan_content_id=ci.id) AS points
              FROM app.content_items ci WHERE status='published' ORDER BY updated_at DESC,id""")).mappings().all()
            groups=defaultdict(list)
            for row in rows:
                values=dict(row);values.pop('id');groups[json.dumps(values,ensure_ascii=False,sort_keys=True,default=str)].append(row)
            duplicates=[g for g in groups.values() if len(g)>1]
            print(f'Групп точных дублей: {len(duplicates)}; лишних копий: {sum(len(g)-1 for g in duplicates)}')
            for g in duplicates:
                keeper=g[0]['id'];print(g[0]['title'],[str(r['id']) for r in g])
                if args.apply:
                    for r in g[1:]:
                        params={'keep':keeper,'copy':r['id']}
                        c.execute(text('INSERT INTO app.favorites(user_id,content_id,created_at) SELECT user_id,:keep,created_at FROM app.favorites WHERE content_id=:copy ON CONFLICT DO NOTHING'),params)
                        c.execute(text('DELETE FROM app.favorites WHERE content_id=:copy'),params)
                        c.execute(text("UPDATE app.content_items SET status='archived',archived_at=now(),updated_at=now() WHERE id=:copy"),params)
            print('Готово.' if args.apply else 'Это проверка: данные не изменены. Для архивации точных копий добавьте --apply.')
    finally:engine.dispose()

if __name__=='__main__':main()
