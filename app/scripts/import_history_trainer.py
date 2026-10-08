from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import get_settings
from app.importing.history_trainer import import_history_trainer, load_history_trainer
from app.importing.sqlite_content import create_sync_engine


def _source_path(value: str) -> Path:
    return Path(value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт тренажёра истории из SQLite")
    parser.add_argument("--source", type=_source_path, required=True, help="Путь к SQLite-базе")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Только проверить базу")
    mode.add_argument("--apply", action="store_true", help="Импортировать в PostgreSQL")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Синхронизировать тренажёр и убрать строки, удалённые из SQLite",
    )
    parser.add_argument("--admin-email", help="Email администратора")
    args = parser.parse_args()
    if args.replace and not args.apply:
        parser.error("Параметр --replace используется только вместе с --apply")

    source = load_history_trainer(args.source)
    print(f"Вопросов: {len(source.questions)}")
    print(f"Предупреждений: {len(source.warnings)}")
    print(f"Ошибок: {len(source.errors)}")
    for message in source.warnings[:30]:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {message}")
    for message in source.errors[:30]:
        print(f"ОШИБКА: {message}")
    if source.errors:
        raise SystemExit(1)
    counts_by_type: dict[int, int] = {}
    for question in source.questions:
        counts_by_type[question.task_number] = counts_by_type.get(question.task_number, 0) + 1
    for task_number, count in sorted(counts_by_type.items()):
        print(f"Задание {task_number}: {count}")
    if not args.apply:
        print("Проверка завершена. PostgreSQL не изменялся.")
        return
    if not args.admin_email:
        parser.error("Для --apply обязательно укажите --admin-email")
    settings = get_settings()
    engine = create_sync_engine(settings.sync_database_url)
    try:
        counts = import_history_trainer(
            engine,
            source,
            args.admin_email,
            settings.media_root,
            replace=args.replace,
        )
    finally:
        engine.dispose()
    print(
        "Импорт завершён: "
        f"добавлено {counts.inserted}, обновлено {counts.updated}, "
        f"пропущено {counts.skipped}, черновиков {counts.drafts}, "
        f"удалено из тренажёра {counts.removed}, "
        f"уникальных изображений {counts.images}."
    )


if __name__ == "__main__":
    main()
