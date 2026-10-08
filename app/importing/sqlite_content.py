from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, create_engine, text

SOURCE_PREFIXES = {
    "history_dates": "history_dates",
    "history_terms": "history_terms",
    "society_terms": "society_obsh",
    "society_plans": "society_plans",
}

SECTION_CODES = {
    "IX-XIV вв": "ix-xiv",
    "IX-XIV вв.": "ix-xiv",
    "XV-XVII вв": "xv-xvii",
    "XV-XVII вв.": "xv-xvii",
    "XVIII век": "xviii",
    "XVIII век.": "xviii",
    "XIX век": "xix",
    "XIX век.": "xix",
    "1-ая половина XX века": "xx-first-half",
    "1-ая половина XX века.": "xx-first-half",
    "2-ая половина XX века": "xx-second-half",
    "2-ая половина XX века.": "xx-second-half",
    "Великая Отечественная война": "wwii",
    "Человек и общество": "human-and-society",
    "Общество": "society",
    "Духовная культура": "spiritual-culture",
    "Социальные отношения": "social-relations",
    "Экономика": "economy",
    "Политика": "politics",
    "Право": "law",
}


class ImportValidationError(ValueError):
    """Raised when a SQLite source cannot be imported safely."""


@dataclass(frozen=True)
class PlanItem:
    text: str
    sort_order: int
    children: tuple[PlanItem, ...] = ()


@dataclass(frozen=True)
class SourceRecord:
    source_name: str
    source_table: str
    source_id: str
    subject_code: str
    section_title: str
    section_code: str
    content_type: str
    title: str
    slug: str
    summary: str | None
    body: dict[str, Any]
    typed: dict[str, Any]
    aliases: tuple[str, ...] = ()
    features: tuple[tuple[str, str, int], ...] = ()
    plan_items: tuple[PlanItem, ...] = ()

    @property
    def source_hash(self) -> str:
        payload = {
            "subject_code": self.subject_code,
            "section_title": self.section_title,
            "section_code": self.section_code,
            "content_type": self.content_type,
            "title": self.title,
            "slug": self.slug,
            "summary": self.summary,
            "body": self.body,
            "typed": self.typed,
            "aliases": self.aliases,
            "features": self.features,
            "plan_items": self.plan_items,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class LoadedSource:
    source_name: str
    path: Path
    checksum_sha256: str
    records: list[SourceRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportCounts:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ImportValidationError(f"Файл не найден: {path}")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _require_columns(connection: sqlite3.Connection, table: str, columns: set[str]) -> None:
    table_row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if table_row is None:
        raise ImportValidationError(f"Отсутствует обязательная таблица {table!r}")
    actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    missing = columns - actual
    if missing:
        raise ImportValidationError(
            f"В таблице {table!r} отсутствуют поля: {', '.join(sorted(missing))}"
        )


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _section_code(title: str) -> str:
    if title in SECTION_CODES:
        return SECTION_CODES[title]
    suffix = hashlib.sha1(title.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"source-{suffix}"


def _parse_iso(value: Any, label: str) -> date | None:
    cleaned = _clean(value)
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ImportValidationError(f"Некорректная ISO-дата {cleaned!r} ({label})") from exc


def _date_precision(label: str, start: date | None, end: date | None) -> str:
    lowered = label.lower()
    if "век" in lowered or "вв" in lowered:
        return "century"
    if "около" in lowered or "примерно" in lowered:
        return "approximate"
    if start is None and end is None:
        return "unknown"
    if start and end and start != end:
        if start.month == 1 and start.day == 1 and end.month == 12 and end.day == 31:
            return "year" if start.year == end.year else "range"
        return "range"
    point = start or end
    if point and point.month == 1 and point.day == 1 and len(label.strip()) <= 12:
        return "year"
    return "day"


def _nonempty(value: Any, field_name: str, row_id: Any) -> str:
    cleaned = _clean(value)
    if not cleaned:
        raise ImportValidationError(f"Запись {row_id}: поле {field_name} пустое")
    return cleaned


def load_history_dates(path: Path) -> LoadedSource:
    result = LoadedSource("history_dates", path, file_sha256(path))
    with _connect_read_only(path) as connection:
        _require_columns(
            connection,
            "dates",
            {"id", "period", "date_text", "date_start_iso", "date_end_iso", "event"},
        )
        rows = connection.execute("SELECT * FROM dates ORDER BY id").fetchall()
        for row in rows:
            try:
                source_id = str(row["id"])
                period = _nonempty(row["period"], "period", source_id)
                label = _nonempty(row["date_text"], "date_text", source_id)
                event = _nonempty(row["event"], "event", source_id)
                start = _parse_iso(row["date_start_iso"], f"dates.id={source_id}")
                end = _parse_iso(row["date_end_iso"], f"dates.id={source_id}")
                if start and end and end < start:
                    raise ImportValidationError(
                        f"Запись {source_id}: конечная дата раньше начальной"
                    )
                display = _clean(row["date_display"]) if "date_display" in row else ""
                source = _clean(row["sourse"]) if "sourse" in row.keys() else (_clean(row["source"]) if "source" in row.keys() else "")
                result.records.append(
                    SourceRecord(
                        source_name=result.source_name,
                        source_table="dates",
                        source_id=source_id,
                        subject_code="history",
                        section_title=period.rstrip("."),
                        section_code=_section_code(period),
                        content_type="date",
                        title=display or label,
                        slug=f"history-date-{source_id}",
                        summary=event,
                        body={"legacy_source": source or None},
                        typed={
                            "date_label": label,
                            "date_start": start.isoformat() if start else None,
                            "date_end": end.isoformat() if end else None,
                            "precision": _date_precision(label, start, end),
                            "event_text": event,
                        },
                    )
                )
            except ImportValidationError as exc:
                result.errors.append(str(exc))
    return result


def load_history_terms(path: Path) -> LoadedSource:
    result = LoadedSource("history_terms", path, file_sha256(path))
    with _connect_read_only(path) as connection:
        _require_columns(connection, "terms", {"id", "period", "term", "definition"})
        for row in connection.execute("SELECT * FROM terms ORDER BY id"):
            try:
                source_id = str(row["id"])
                period = _nonempty(row["period"], "period", source_id)
                term = _nonempty(row["term"], "term", source_id)
                definition = _nonempty(row["definition"], "definition", source_id)
                source = _clean(row["sourse"]) if "sourse" in row.keys() else (_clean(row["source"]) if "source" in row.keys() else "")
                result.records.append(
                    SourceRecord(
                        source_name=result.source_name,
                        source_table="terms",
                        source_id=source_id,
                        subject_code="history",
                        section_title=period.rstrip("."),
                        section_code=_section_code(period),
                        content_type="term",
                        title=term,
                        slug=f"history-term-{source_id}",
                        summary=definition,
                        body={"legacy_source": source or None},
                        typed={"definition": definition},
                    )
                )
            except ImportValidationError as exc:
                result.errors.append(str(exc))
    return result


def load_society_terms(path: Path) -> LoadedSource:
    result = LoadedSource("society_terms", path, file_sha256(path))
    with _connect_read_only(path) as connection:
        _require_columns(connection, "terms", {"id", "term", "section"})
        _require_columns(
            connection,
            "term_characteristics",
            {"term_id", "order_num", "text", "marker", "source_ref"},
        )
        for row in connection.execute("SELECT * FROM terms ORDER BY id"):
            try:
                source_id = str(row["id"])
                term = _nonempty(row["term"], "term", source_id)
                section = _nonempty(row["section"], "section", source_id)
                characteristics = connection.execute(
                    """
                    SELECT order_num, text, marker, source_ref
                    FROM term_characteristics
                    WHERE term_id = ?
                    ORDER BY order_num, id
                    """,
                    (row["id"],),
                ).fetchall()
                if not characteristics:
                    raise ImportValidationError(f"Термин {source_id} не содержит характеристик")
                features: list[tuple[str, str, int]] = []
                feature_texts: list[str] = []
                feature_meta: list[dict[str, Any]] = []
                for characteristic in characteristics:
                    feature_text = _nonempty(
                        characteristic["text"], "term_characteristics.text", source_id
                    )
                    marker = _clean(characteristic["marker"]) or "feature"
                    order_num = int(characteristic["order_num"])
                    features.append((marker, feature_text, order_num))
                    feature_texts.append(feature_text)
                    feature_meta.append(
                        {
                            "order": order_num,
                            "marker": None if marker == "feature" else marker,
                            "source_ref": _clean(characteristic["source_ref"]) or None,
                        }
                    )
                definition = "; ".join(feature_texts)
                result.records.append(
                    SourceRecord(
                        source_name=result.source_name,
                        source_table="terms",
                        source_id=source_id,
                        subject_code="society",
                        section_title=section,
                        section_code=_section_code(section),
                        content_type="term",
                        title=term,
                        slug=f"society-term-{source_id}",
                        summary=feature_texts[0],
                        body={
                            "legacy_sources": _clean(row["sources"]) or None,
                            "feature_metadata": feature_meta,
                        },
                        typed={"definition": definition},
                        features=tuple(features),
                    )
                )
            except ImportValidationError as exc:
                result.errors.append(str(exc))
    return result


def load_society_plans(path: Path) -> LoadedSource:
    result = LoadedSource("society_plans", path, file_sha256(path))
    with _connect_read_only(path) as connection:
        _require_columns(connection, "plans", {"id", "title", "topic"})
        plan_columns = {row[1] for row in connection.execute("PRAGMA table_info(plans)")}
        source_column = "sourses" if "sourses" in plan_columns else "sources"
        _require_columns(connection, "plans", {source_column})
        _require_columns(connection, "plan_alt_titles", {"plan_id", "title"})
        _require_columns(
            connection,
            "points",
            {"id", "plan_id", "number", "text", "is_required", "source_ref"},
        )
        _require_columns(connection, "subpoints", {"point_id", "letter", "text"})
        has_requirements = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plan_requirements'").fetchone() is not None
        for row in connection.execute("SELECT * FROM plans ORDER BY id"):
            try:
                source_id = str(row["id"])
                title = _nonempty(row["title"], "title", source_id)
                topic = _nonempty(row["topic"], "topic", source_id)
                aliases = tuple(
                    _clean(item["title"])
                    for item in connection.execute(
                        "SELECT title FROM plan_alt_titles WHERE plan_id = ? ORDER BY id",
                        (row["id"],),
                    )
                    if _clean(item["title"])
                )
                plan_items: list[PlanItem] = []
                points_meta: list[dict[str, Any]] = []
                points = connection.execute(
                    "SELECT * FROM points WHERE plan_id = ? ORDER BY number, id", (row["id"],)
                ).fetchall()
                if not points:
                    raise ImportValidationError(f"План {source_id} не содержит пунктов")
                for point in points:
                    point_text = _nonempty(point["text"], "points.text", point["id"])
                    children = tuple(
                        PlanItem(
                            text=_nonempty(subpoint["text"], "subpoints.text", subpoint["id"]),
                            sort_order=index,
                        )
                        for index, subpoint in enumerate(
                            connection.execute(
                                "SELECT * FROM subpoints WHERE point_id = ? ORDER BY id",
                                (point["id"],),
                            ),
                            start=1,
                        )
                    )
                    number = int(point["number"])
                    plan_items.append(PlanItem(point_text, number, children))
                    points_meta.append(
                        {
                            "number": number,
                            "is_required": bool(point["is_required"]),
                            "source_ref": None,
                        }
                    )
                body_text = " ".join(item.text for item in plan_items)
                result.records.append(
                    SourceRecord(
                        source_name=result.source_name,
                        source_table="plans",
                        source_id=source_id,
                        subject_code="society",
                        section_title=topic,
                        section_code=_section_code(topic),
                        content_type="plan",
                        title=title,
                        slug=f"society-plan-{source_id}",
                        summary=body_text[:500] or None,
                        body={
                            "legacy_sources": _clean(row[source_column]) or None,
                            "points_metadata": points_meta,
                            "requirement_notes": [item[0] for item in connection.execute("SELECT note FROM plan_requirements WHERE plan_id = ?", (row["id"],))] if has_requirements else [],
                        },
                        typed={"introduction": None, "conclusion": None},
                        aliases=aliases,
                        plan_items=tuple(plan_items),
                    )
                )
            except ImportValidationError as exc:
                result.errors.append(str(exc))
    return result


LOADERS = {
    "history_dates": load_history_dates,
    "history_terms": load_history_terms,
    "society_terms": load_society_terms,
    "society_plans": load_society_plans,
}


def discover_source_files(source_dir: Path) -> dict[str, Path]:
    if not source_dir.is_dir():
        raise ImportValidationError(f"Папка не найдена: {source_dir}")
    discovered: dict[str, Path] = {}
    for source_name, prefix in SOURCE_PREFIXES.items():
        matches = sorted(
            path for path in source_dir.glob("*.db") if path.stem.lower().startswith(prefix)
        )
        if not matches:
            raise ImportValidationError(
                f"В папке {source_dir} не найден файл, начинающийся с {prefix!r}"
            )
        if len(matches) > 1:
            names = ", ".join(path.name for path in matches)
            raise ImportValidationError(f"Найдено несколько файлов {prefix!r}: {names}")
        discovered[source_name] = matches[0]
    return discovered


def load_all_sources(paths: dict[str, Path]) -> list[LoadedSource]:
    return [LOADERS[name](paths[name]) for name in SOURCE_PREFIXES]


def create_sync_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _resolve_admin(connection: Connection, email: str) -> UUID:
    row = connection.execute(
        text(
            """
            SELECT id
            FROM app.users
            WHERE lower(email) = lower(:email)
              AND role = 'admin'
              AND status = 'active'
            """
        ),
        {"email": email},
    ).one_or_none()
    if row is None:
        raise ImportValidationError(
            f"Активный администратор {email!r} не найден. "
            "Сначала выполните bootstrap_admin с параметром --create-if-missing."
        )
    return row[0]


def _start_run(
    engine: Engine, source: LoadedSource, actor_id: UUID
) -> UUID:
    with engine.begin() as connection:
        return connection.execute(
            text(
                """
                INSERT INTO app.import_runs (
                    source_name, source_filename, source_checksum_sha256,
                    dry_run, status, started_by
                )
                VALUES (:source_name, :filename, :checksum, false, 'running', :actor_id)
                RETURNING id
                """
            ),
            {
                "source_name": source.source_name,
                "filename": source.path.name,
                "checksum": source.checksum_sha256,
                "actor_id": actor_id,
            },
        ).scalar_one()


def _finish_run(
    engine: Engine,
    run_id: UUID,
    status: str,
    counts: ImportCounts,
    errors: list[str],
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE app.import_runs
                SET status = CAST(:status AS app.import_status),
                    completed_at = now(),
                    inserted_count = :inserted,
                    updated_count = :updated,
                    skipped_count = :skipped,
                    failed_count = :failed,
                    error_report = CAST(:errors AS jsonb)
                WHERE id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "inserted": counts.inserted,
                "updated": counts.updated,
                "skipped": counts.skipped,
                "failed": counts.failed,
                "errors": _json(errors),
            },
        )


def _ensure_section(connection: Connection, record: SourceRecord) -> UUID:
    subject_id = connection.execute(
        text("SELECT id FROM app.subjects WHERE code = :code"),
        {"code": record.subject_code},
    ).scalar_one()
    return connection.execute(
        text(
            """
            INSERT INTO app.sections (subject_id, code, title)
            VALUES (:subject_id, :code, :title)
            ON CONFLICT (subject_id, code) DO UPDATE
            SET title = EXCLUDED.title,
                is_active = true
            RETURNING id
            """
        ),
        {
            "subject_id": subject_id,
            "code": record.section_code,
            "title": record.section_title,
        },
    ).scalar_one()


def _search_body(record: SourceRecord) -> str:
    parts: list[str] = []
    if record.summary:
        parts.append(record.summary)
    parts.extend(str(value) for value in record.typed.values() if value)
    parts.extend(feature[1] for feature in record.features)

    def add_plan_items(items: Iterable[PlanItem]) -> None:
        for item in items:
            parts.append(item.text)
            add_plan_items(item.children)

    add_plan_items(record.plan_items)
    return " ".join(parts)


def _save_common(
    connection: Connection,
    record: SourceRecord,
    content_id: UUID,
    section_id: UUID,
    actor_id: UUID,
    exists: bool,
) -> None:
    parameters = {
        "id": content_id,
        "subject_code": record.subject_code,
        "section_id": section_id,
        "content_type": record.content_type,
        "title": record.title,
        "slug": record.slug,
        "summary": record.summary,
        "body": _json(record.body),
        "actor_id": actor_id,
    }
    if exists:
        connection.execute(
            text(
                """
                UPDATE app.content_items
                SET section_id = :section_id,
                    content_type_code = :content_type,
                    title = :title,
                    slug = :slug,
                    summary = :summary,
                    body = CAST(:body AS jsonb),
                    status = 'published',
                    updated_by = :actor_id,
                    published_at = COALESCE(published_at, now()),
                    archived_at = NULL
                WHERE id = :id
                """
            ),
            parameters,
        )
    else:
        connection.execute(
            text(
                """
                INSERT INTO app.content_items (
                    id, subject_id, section_id, content_type_code, title, slug,
                    summary, body, status, created_by, updated_by, published_at
                )
                SELECT :id, id, :section_id, :content_type, :title, :slug,
                       :summary, CAST(:body AS jsonb), 'published', :actor_id,
                       :actor_id, now()
                FROM app.subjects
                WHERE code = :subject_code
                """
            ),
            parameters,
        )

    connection.execute(
        text("DELETE FROM app.content_aliases WHERE content_id = :content_id"),
        {"content_id": content_id},
    )
    for alias in dict.fromkeys(record.aliases):
        connection.execute(
            text(
                "INSERT INTO app.content_aliases (content_id, alias) "
                "VALUES (:content_id, :alias)"
            ),
            {"content_id": content_id, "alias": alias},
        )
    connection.execute(
        text(
            """
            INSERT INTO app.content_search_documents (
                content_id, title_text, aliases_text, body_text
            )
            VALUES (:content_id, :title, :aliases, :body)
            ON CONFLICT (content_id) DO UPDATE
            SET title_text = EXCLUDED.title_text,
                aliases_text = EXCLUDED.aliases_text,
                body_text = EXCLUDED.body_text,
                updated_at = now()
            """
        ),
        {
            "content_id": content_id,
            "title": record.title,
            "aliases": " ".join(record.aliases),
            "body": _search_body(record),
        },
    )


def _save_typed(connection: Connection, record: SourceRecord, content_id: UUID) -> None:
    if record.content_type == "date":
        connection.execute(
            text(
                """
                INSERT INTO app.history_dates (
                    content_id, date_label, date_start, date_end, precision, event_text
                )
                VALUES (
                    :content_id, :date_label, :date_start, :date_end,
                    CAST(:precision AS app.date_precision), :event_text
                )
                ON CONFLICT (content_id) DO UPDATE
                SET date_label = EXCLUDED.date_label,
                    date_start = EXCLUDED.date_start,
                    date_end = EXCLUDED.date_end,
                    precision = EXCLUDED.precision,
                    event_text = EXCLUDED.event_text
                """
            ),
            {"content_id": content_id, **record.typed},
        )
        return
    if record.content_type == "term":
        connection.execute(
            text(
                """
                INSERT INTO app.terms (content_id, definition)
                VALUES (:content_id, :definition)
                ON CONFLICT (content_id) DO UPDATE
                SET definition = EXCLUDED.definition
                """
            ),
            {"content_id": content_id, **record.typed},
        )
        connection.execute(
            text("DELETE FROM app.term_features WHERE term_content_id = :content_id"),
            {"content_id": content_id},
        )
        for feature_type, feature_text, sort_order in record.features:
            connection.execute(
                text(
                    """
                    INSERT INTO app.term_features (
                        term_content_id, feature_type, feature_text, sort_order
                    )
                    VALUES (:content_id, :feature_type, :feature_text, :sort_order)
                    """
                ),
                {
                    "content_id": content_id,
                    "feature_type": feature_type,
                    "feature_text": feature_text,
                    "sort_order": sort_order,
                },
            )
        return
    if record.content_type == "plan":
        connection.execute(
            text(
                """
                INSERT INTO app.plans (content_id, introduction, conclusion)
                VALUES (:content_id, :introduction, :conclusion)
                ON CONFLICT (content_id) DO UPDATE
                SET introduction = EXCLUDED.introduction,
                    conclusion = EXCLUDED.conclusion
                """
            ),
            {"content_id": content_id, **record.typed},
        )
        connection.execute(
            text("DELETE FROM app.plan_items WHERE plan_content_id = :content_id"),
            {"content_id": content_id},
        )
        for item in record.plan_items:
            parent_id = connection.execute(
                text(
                    """
                    INSERT INTO app.plan_items (
                        plan_content_id, item_text, sort_order
                    )
                    VALUES (:content_id, :item_text, :sort_order)
                    RETURNING id
                    """
                ),
                {
                    "content_id": content_id,
                    "item_text": item.text,
                    "sort_order": item.sort_order,
                },
            ).scalar_one()
            for child in item.children:
                connection.execute(
                    text(
                        """
                        INSERT INTO app.plan_items (
                            plan_content_id, parent_id, item_text, sort_order
                        )
                        VALUES (:content_id, :parent_id, :item_text, :sort_order)
                        """
                    ),
                    {
                        "content_id": content_id,
                        "parent_id": parent_id,
                        "item_text": child.text,
                        "sort_order": child.sort_order,
                    },
                )


def _import_record(
    connection: Connection,
    record: SourceRecord,
    run_id: UUID,
    actor_id: UUID,
) -> str:
    mapping = connection.execute(
        text(
            """
            SELECT sm.entity_id, sm.source_hash,
                   EXISTS (SELECT 1 FROM app.content_items ci WHERE ci.id = sm.entity_id)
            FROM app.source_mappings sm
            WHERE sm.source_name = :source_name
              AND sm.source_table = :source_table
              AND sm.source_record_id = :source_id
            """
        ),
        {
            "source_name": record.source_name,
            "source_table": record.source_table,
            "source_id": record.source_id,
        },
    ).one_or_none()
    if mapping and mapping[1] == record.source_hash and mapping[2]:
        connection.execute(
            text(
                """
                UPDATE app.source_mappings
                SET last_import_run_id = :run_id,
                    last_imported_at = now()
                WHERE source_name = :source_name
                  AND source_table = :source_table
                  AND source_record_id = :source_id
                """
            ),
            {
                "run_id": run_id,
                "source_name": record.source_name,
                "source_table": record.source_table,
                "source_id": record.source_id,
            },
        )
        return "skipped"

    exists = bool(mapping and mapping[2])
    content_id = mapping[0] if exists else uuid4()
    section_id = _ensure_section(connection, record)
    _save_common(connection, record, content_id, section_id, actor_id, exists)
    _save_typed(connection, record, content_id)
    connection.execute(
        text(
            """
            INSERT INTO app.source_mappings (
                source_name, source_table, source_record_id, entity_type,
                entity_id, source_hash, last_import_run_id
            )
            VALUES (
                :source_name, :source_table, :source_id, 'content_item',
                :entity_id, :source_hash, :run_id
            )
            ON CONFLICT (source_name, source_table, source_record_id) DO UPDATE
            SET entity_type = EXCLUDED.entity_type,
                entity_id = EXCLUDED.entity_id,
                source_hash = EXCLUDED.source_hash,
                last_import_run_id = EXCLUDED.last_import_run_id,
                last_imported_at = now()
            """
        ),
        {
            "source_name": record.source_name,
            "source_table": record.source_table,
            "source_id": record.source_id,
            "entity_id": content_id,
            "source_hash": record.source_hash,
            "run_id": run_id,
        },
    )
    return "updated" if exists else "inserted"


def import_sources(
    engine: Engine, sources: list[LoadedSource], admin_email: str
) -> dict[str, ImportCounts]:
    invalid = [source for source in sources if source.errors]
    if invalid:
        names = ", ".join(source.source_name for source in invalid)
        raise ImportValidationError(f"Импорт отменён: ошибки в источниках {names}")
    with engine.connect() as connection:
        actor_id = _resolve_admin(connection, admin_email)

    result: dict[str, ImportCounts] = {}
    for source in sources:
        counts = ImportCounts()
        run_id = _start_run(engine, source, actor_id)
        try:
            with engine.begin() as connection:
                for record in source.records:
                    outcome = _import_record(connection, record, run_id, actor_id)
                    setattr(counts, outcome, getattr(counts, outcome) + 1)
            _finish_run(engine, run_id, "completed", counts, [])
        except Exception as exc:
            counts.failed += 1
            _finish_run(engine, run_id, "failed", counts, [str(exc)])
            raise
        result[source.source_name] = counts
    return result
