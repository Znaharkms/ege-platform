from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import get_settings
from app.importing.sqlite_content import (
    ImportValidationError,
    create_sync_engine,
    discover_source_files,
    import_sources,
    load_all_sources,
)


def _print_validation(sources) -> None:
    print("Проверка SQLite-источников:")
    for source in sources:
        state = "OK" if not source.errors else "ОШИБКИ"
        print(
            f"- {source.source_name}: {len(source.records)} записей, "
            f"{len(source.errors)} ошибок [{state}]"
        )
        for error in source.errors[:20]:
            print(f"    {error}")
        if len(source.errors) > 20:
            print(f"    ... ещё {len(source.errors) - 20}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Проверить и импортировать учебный контент из SQLite в PostgreSQL"
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Только проверить источники")
    mode.add_argument("--apply", action="store_true", help="Записать данные в PostgreSQL")
    parser.add_argument("--admin-email", help="Email администратора для аудита импорта")
    args = parser.parse_args()

    try:
        paths = discover_source_files(args.source_dir)
        sources = load_all_sources(paths)
        _print_validation(sources)
        if any(source.errors for source in sources):
            raise SystemExit(1)
        if not args.apply:
            print("Dry-run завершён: PostgreSQL не изменялся.")
            return
        if not args.admin_email:
            parser.error("Для --apply обязательно укажите --admin-email")
        settings = get_settings()
        engine = create_sync_engine(settings.sync_database_url)
        try:
            results = import_sources(engine, sources, args.admin_email)
        finally:
            engine.dispose()
        print("Импорт завершён:")
        for source_name, counts in results.items():
            print(
                f"- {source_name}: добавлено {counts.inserted}, "
                f"обновлено {counts.updated}, пропущено {counts.skipped}"
            )
    except ImportValidationError as exc:
        raise SystemExit(f"Ошибка импорта: {exc}") from exc


if __name__ == "__main__":
    main()

