"""Update history terms only, including sources from terms.sourse."""
import argparse
from pathlib import Path
from app.core.config import get_settings
from app.importing.sqlite_content import load_history_terms,create_sync_engine,import_sources

def main():
    parser=argparse.ArgumentParser(description='Импорт терминов истории с источниками')
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--admin-email')
    args=parser.parse_args()
    if not args.source.is_file():parser.error(f'База не найдена: {args.source.resolve()}')
    source=load_history_terms(args.source)
    if source.errors:raise SystemExit('\n'.join(source.errors))
    print(f'Проверено терминов: {len(source.records)}')
    print(f'С источниками: {sum(bool(r.body.get("legacy_source")) for r in source.records)}')
    if not args.apply:return
    if not args.admin_email:parser.error('--apply требует --admin-email')
    engine=create_sync_engine(get_settings().sync_database_url)
    try:print(import_sources(engine,[source],args.admin_email))
    finally:engine.dispose()

if __name__=='__main__':main()
