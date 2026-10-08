"""Import only corrected society plans, preserving existing content IDs by title."""
import argparse
import re
from dataclasses import replace
from pathlib import Path
from sqlalchemy import text
from app.core.config import get_settings
from app.importing.sqlite_content import load_society_plans, create_sync_engine, import_sources, ImportValidationError


def keys(title):
    values = re.findall(r'«([^»]+)»', title) or [re.sub(r'^План(?: по теме)?\s*', '', title)]
    return {re.sub(r'\s+', ' ', value).strip(' «»"').casefold().replace('ё', 'е') for value in values}


def main():
    p = argparse.ArgumentParser(description='Import corrected society plans only')
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--replace-all', action='store_true', help='Publish the rebuilt set and archive the previous plans')
    p.add_argument('--admin-email')
    args = p.parse_args()
    if not args.source.is_file():
        p.error(f"Файл базы не найден: {args.source.resolve()}")
    source = load_society_plans(args.source)
    if source.errors:
        raise SystemExit('\n'.join(source.errors))
    print(f'Проверено планов: {len(source.records)}')
    if not args.apply:
        return
    if not args.admin_email:
        p.error('--apply требует --admin-email')
    engine = create_sync_engine(get_settings().sync_database_url)
    try:
        if args.replace_all:
            source.source_name='society_plans_pdf_v2'
            source.records=[replace(r,source_name=source.source_name,slug=f'society-plan-pdf-{r.source_id}') for r in source.records]
            print(import_sources(engine,[source],args.admin_email))
            with engine.begin() as conn:
                count=conn.execute(text("""UPDATE app.content_items SET status='archived', archived_at=COALESCE(archived_at,now()), updated_at=now()
                    WHERE subject_id=(SELECT id FROM app.subjects WHERE code='society') AND content_type_code='plan' AND status <> 'archived'
                    AND id NOT IN (SELECT entity_id FROM app.source_mappings WHERE source_name='society_plans_pdf_v2' AND source_table='plans')""")).rowcount
            print(f'Предыдущих планов перенесено в архив: {count}')
            return
        with engine.connect() as conn:
            rows = list(conn.execute(text("""SELECT ci.id, ci.title, ci.slug, sm.source_record_id
                FROM app.content_items ci JOIN app.subjects s ON s.id=ci.subject_id
                LEFT JOIN app.source_mappings sm ON sm.entity_id=ci.id AND sm.source_name='society_plans' AND sm.source_table='plans'
                WHERE s.code='society' AND ci.content_type_code='plan'""")).mappings())
        next_id = max([int(r['source_record_id']) for r in rows if str(r['source_record_id']).isdigit()] or [0]) + 1
        records = []; used = set()
        for record in source.records:
            matches = [r for r in rows if keys(record.title) & keys(r['title'])]
            if len(matches)>1:
                raise ImportValidationError(f'Неоднозначное совпадение: {record.title}. Импорт не начат.')
            if matches:
                row=matches[0]
                if not row['source_record_id'] or row['id'] in used:
                    raise ImportValidationError(f'Не удалось однозначно обновить: {record.title}. Импорт не начат.')
                used.add(row['id']); record=replace(record,source_id=str(row['source_record_id']),slug=row['slug'])
            else:
                record=replace(record,source_id=str(next_id),slug=f'society-plan-{next_id}'); next_id+=1
            records.append(record)
        source.records=records
        print(import_sources(engine,[source],args.admin_email))
    finally:
        engine.dispose()

if __name__=='__main__':
    main()
