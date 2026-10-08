from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError
from sqlalchemy import Connection, Engine, text

from app.importing.sqlite_content import ImportValidationError


@dataclass(frozen=True)
class TrainerQuestion:
    source_id: int
    task_number: int
    question_type: str
    prompt: dict[str, Any]
    answer_spec: dict[str, Any]
    explanation: dict[str, Any]
    period: str | None
    categories: tuple[str, ...]
    stimulus_key: str | None
    image_bytes: bytes | None
    publishable: bool
    warnings: tuple[str, ...] = ()


@dataclass
class TrainerSource:
    path: Path
    questions: list[TrainerQuestion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TrainerImportCounts:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    drafts: int = 0
    images: int = 0
    removed: int = 0


REQUIRED_COLUMNS = {
    "id", "question", "type_data_1", "data_1", "type_data_2", "data_2",
    "tale", "answer", "numb_type", "image", "period",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\u2060", "").split())


def _clean_multiline(value: Any) -> str:
    source = str(value or "").replace("\u2060", "").replace("\r\n", "\n")
    lines = [_clean(line) for line in source.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _repair_literal_escapes(value: str) -> str:
    """Repair escape sequences split by whitespace in exported SQLite text."""

    repaired = re.sub(
        r"\\u\s*([0-9a-fA-F])\s*([0-9a-fA-F])\s*([0-9a-fA-F])\s*([0-9a-fA-F])",
        lambda match: "\\u" + "".join(match.groups()),
        value,
    )
    repaired = re.sub(
        r"\\x\s*([0-9a-fA-F])\s*([0-9a-fA-F])",
        lambda match: "\\x" + "".join(match.groups()),
        repaired,
    )
    repaired = re.sub(r"\\u(?![0-9a-fA-F]{4})", r"\\\\u", repaired)
    repaired = re.sub(r"\\U(?![0-9a-fA-F]{8})", r"\\\\U", repaired)
    return re.sub(r"\\x(?![0-9a-fA-F]{2})", r"\\\\x", repaired)


def _prepare_source_image(value: bytes | None) -> tuple[bytes | None, str | None]:
    if not value:
        return None, None
    try:
        with Image.open(BytesIO(value)) as image:
            ImageOps.exif_transpose(image).load()
        return value, None
    except (OSError, SyntaxError, ValueError, UnidentifiedImageError):
        previous = ImageFile.LOAD_TRUNCATED_IMAGES
        try:
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            with Image.open(BytesIO(value)) as image:
                repaired = ImageOps.exif_transpose(image)
                repaired.load()
                if repaired.mode not in {"RGB", "RGBA"}:
                    repaired = repaired.convert(
                        "RGBA" if "transparency" in repaired.info else "RGB"
                    )
                output = BytesIO()
                repaired.save(output, format="PNG", optimize=True)
            return output.getvalue(), "повреждённая картинка восстановлена; проверьте её"
        except (OSError, SyntaxError, ValueError, UnidentifiedImageError):
            return None, "повреждённая картинка не восстановлена"
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = previous


def _dict(value: Any) -> dict[Any, Any]:
    if value is None or not str(value).strip():
        return {}
    parsed = ast.literal_eval(_repair_literal_escapes(str(value)))
    if not isinstance(parsed, dict):
        raise ValueError("ожидался словарь")
    return parsed


def _values(value: Any) -> list[str]:
    return [_clean(item) for item in _dict(value).values() if _clean(item)]


def _answer_variants(value: Any) -> list[str]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return []
    try:
        parsed = ast.literal_eval(cleaned)
        if isinstance(parsed, list):
            return [_clean(item) for item in parsed if _clean(item)]
        if isinstance(parsed, (str, int)):
            return [_clean(parsed)]
    except (SyntaxError, ValueError):
        quoted = [_clean(item) for item in re.findall(r"['\"]([^'\"]+)['\"]", cleaned)]
        if quoted:
            return list(dict.fromkeys(quoted))
    return [_clean(cleaned.strip("[]'\" "))]


def _categories(task_number: int) -> tuple[str, ...]:
    values: list[str] = []
    if task_number == 4:
        values.append("tables")
    if task_number in {7, 15, 16}:
        values.append("culture")
    if task_number == 8:
        values.append("wwii")
    if task_number in {9, 10, 11, 12}:
        values.append("maps")
    return tuple(values)


def _structured_prompt(row: sqlite3.Row, task_number: int, image: bytes | None) -> dict[str, Any]:
    question = (
        _clean_multiline(row["question"])
        if task_number in {13, 14, 18, 19, 20}
        else _clean(row["question"])
    )
    first = _values(row["data_1"])
    second = _values(row["data_2"])
    if task_number in {8, 9, 10, 11, 12} and first:
        question = "\n".join([part for part in [question, *first] if part])
    prompt: dict[str, Any] = {"text": question, "taskNumber": task_number}
    if task_number in {1, 3, 5, 7}:
        prompt["leftTitle"] = _clean(row["type_data_1"])
        prompt["rightTitle"] = _clean(row["type_data_2"])
        prompt["leftItems"] = [
            {"id": f"left-{index}", "text": value}
            for index, value in enumerate(first, 1)
        ]
        prompt["rightItems"] = [
            {"id": f"right-{index}", "text": value}
            for index, value in enumerate(second, 1)
        ]
    elif task_number == 2:
        prompt["items"] = [
            {"id": f"item-{index}", "text": value}
            for index, value in enumerate(first, 1)
        ]
    elif task_number == 6:
        prompt["options"] = [
            {"id": f"option-{index}", "text": value}
            for index, value in enumerate(first, 1)
        ]
    elif first and task_number not in {8, 9, 10, 11, 12}:
        prompt["sourceItems"] = first
    if image:
        checksum = hashlib.sha256(image).hexdigest()
        prompt["image"] = {"checksumSha256": checksum}
    return prompt


def _answer_spec(
    row: sqlite3.Row,
    task_number: int,
    prompt: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    answer = str(row["answer"] or "").strip()
    if task_number in {1, 3, 5, 7}:
        pairs = {
            f"left-{index}": f"right-{digit}"
            for index, digit in enumerate(answer, 1)
            if digit.isdigit()
        }
        return "matching", {"pairs": pairs}
    if task_number == 2:
        return "ordering", {
            "correctOrder": [f"item-{digit}" for digit in answer if digit.isdigit()]
        }
    if task_number == 6:
        return "multiple_choice", {
            "correctOptionIds": [f"option-{digit}" for digit in answer if digit.isdigit()]
        }
    if task_number <= 12:
        return "text", {"acceptedAnswers": _answer_variants(answer), "caseSensitive": False}
    return "self_check", {"modelAnswer": _values(row["tale"])}


def load_history_trainer(path: Path) -> TrainerSource:
    source = TrainerSource(path=path)
    if not path.is_file():
        source.errors.append(f"Файл не найден: {path}")
        return source
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            source.errors.append(f"Проверка целостности SQLite: {integrity}")
            return source
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Question'"
        ).fetchone()
        if table is None:
            source.errors.append("Не найдена таблица Question")
            return source
        actual = {row[1] for row in connection.execute('PRAGMA table_info("Question")')}
        missing = REQUIRED_COLUMNS - actual
        if missing:
            source.errors.append(f"Отсутствуют поля: {', '.join(sorted(missing))}")
            return source
        seen: set[str] = set()
        for row in connection.execute('SELECT * FROM "Question" ORDER BY id'):
            row_warnings: list[str] = []
            try:
                source_id = int(row["id"])
                match = re.search(r"(\d+)", str(row["numb_type"] or ""))
                if match is None:
                    raise ValueError("не определён номер задания")
                task_number = int(match.group(1))
                image_source = (
                    bytes(row["image"])
                    if isinstance(row["image"], bytes) and row["image"]
                    else None
                )
                image, image_warning = _prepare_source_image(image_source)
                if image_warning:
                    row_warnings.append(image_warning)
                prompt = _structured_prompt(row, task_number, image)
                question_type, answer_spec = _answer_spec(row, task_number, prompt)
                explanation_values = _values(row["tale"])
                explanation = {"text": "\n".join(explanation_values)}
                period = _clean(row["period"]) or None
                stimulus_key = hashlib.sha256(image).hexdigest() if image else None
                publishable = bool(_clean(prompt.get("text"))) and bool(answer_spec)
                if image_warning:
                    publishable = False
                if question_type == "self_check" and not answer_spec["modelAnswer"]:
                    publishable = False
                    row_warnings.append("нет эталонного ответа")
                if question_type == "text" and not answer_spec["acceptedAnswers"]:
                    publishable = False
                    row_warnings.append("нет правильного ответа")
                signature = hashlib.sha256(
                    json.dumps(
                        {"type": question_type, "prompt": prompt, "answer": answer_spec},
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                if signature in seen:
                    publishable = False
                    row_warnings.append("полный дубликат")
                seen.add(signature)
                source.questions.append(
                    TrainerQuestion(
                        source_id=source_id,
                        task_number=task_number,
                        question_type=question_type,
                        prompt=prompt,
                        answer_spec=answer_spec,
                        explanation=explanation,
                        period=period,
                        categories=_categories(task_number),
                        stimulus_key=stimulus_key,
                        image_bytes=image,
                        publishable=publishable,
                        warnings=tuple(row_warnings),
                    )
                )
                for warning in row_warnings:
                    source.warnings.append(f"Question.id={source_id}: {warning}")
            except (ValueError, SyntaxError) as exc:
                source.errors.append(f"Question.id={row['id']}: {exc}")
    finally:
        connection.close()
    return source


def _store_image(
    connection: Connection,
    media_root: Path,
    admin_id: UUID,
    question: TrainerQuestion,
) -> dict[str, Any] | None:
    if question.image_bytes is None:
        return None
    source_checksum = hashlib.sha256(question.image_bytes).hexdigest()
    object_key = f"history-trainer/{source_checksum}.webp"
    existing = connection.execute(
        text("SELECT * FROM app.media_assets WHERE object_key = :object_key LIMIT 1"),
        {"object_key": object_key},
    ).mappings().one_or_none()
    if existing is not None:
        return dict(existing)
    with Image.open(BytesIO(question.image_bytes)) as image:
        image.seek(0)
        prepared = ImageOps.exif_transpose(image)
        prepared.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        if prepared.mode not in {"RGB", "RGBA"}:
            prepared = prepared.convert("RGBA" if "transparency" in prepared.info else "RGB")
        output = BytesIO()
        prepared.save(output, format="WEBP", quality=88, method=6)
        optimized = output.getvalue()
        width, height = prepared.size
    checksum = hashlib.sha256(optimized).hexdigest()
    target = media_root.resolve() / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(optimized)
    asset_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO app.media_assets (
                id, object_key, original_filename, mime_type, byte_size,
                width, height, checksum_sha256, uploaded_by
            ) VALUES (
                :id, :object_key, :filename, :mime_type, :byte_size,
                :width, :height, :checksum, :admin_id
            )
            """
        ),
        {
            "id": asset_id,
            "object_key": object_key,
            "filename": f"history-question-{question.source_id}.webp",
            "mime_type": "image/webp",
            "byte_size": len(optimized),
            "width": width,
            "height": height,
            "checksum": checksum,
            "admin_id": admin_id,
        },
    )
    return {
        "id": asset_id,
        "object_key": object_key,
        "original_filename": f"history-question-{question.source_id}.webp",
        "mime_type": "image/webp",
        "byte_size": len(optimized),
        "width": width,
        "height": height,
        "checksum_sha256": checksum,
    }


def import_history_trainer(
    engine: Engine,
    source: TrainerSource,
    admin_email: str,
    media_root: Path,
    replace: bool = False,
) -> TrainerImportCounts:
    if source.errors:
        raise ImportValidationError("В SQLite найдены ошибки; сначала выполните --dry-run")
    counts = TrainerImportCounts()
    with engine.begin() as connection:
        admin_id = connection.execute(
            text("SELECT id FROM app.users WHERE lower(email)=lower(:email) AND role='admin'"),
            {"email": admin_email},
        ).scalar_one_or_none()
        if admin_id is None:
            raise ImportValidationError("Активный администратор с указанным email не найден")
        subject_id = connection.execute(
            text("SELECT id FROM app.subjects WHERE code='history'")
        ).scalar_one()
        for item in source.questions:
            asset = _store_image(connection, media_root, admin_id, item)
            prompt = dict(item.prompt)
            if asset:
                prompt["image"] = {
                    "id": str(asset["id"]),
                    "url": f"/media/{asset['object_key']}",
                    "filename": asset["original_filename"],
                    "mimeType": asset["mime_type"],
                    "byteSize": asset["byte_size"],
                    "width": asset["width"],
                    "height": asset["height"],
                    "checksumSha256": asset["checksum_sha256"],
                }
            existing = connection.execute(
                text(
                    """
                    SELECT metadata.question_id, question.current_version,
                           version.prompt, version.answer_spec, version.explanation
                    FROM app.history_question_metadata metadata
                    JOIN app.questions question ON question.id=metadata.question_id
                    JOIN app.question_versions version
                      ON version.question_id=question.id
                     AND version.version=question.current_version
                    WHERE metadata.source_row_id=:source_id
                    """
                ),
                {"source_id": item.source_id},
            ).mappings().one_or_none()
            status_value = "published" if item.publishable else "draft"
            if not item.publishable:
                counts.drafts += 1
            payload_same = existing and all(
                [
                    existing["prompt"] == prompt,
                    existing["answer_spec"] == item.answer_spec,
                    existing["explanation"] == item.explanation,
                ]
            )
            if payload_same:
                question_id = existing["question_id"]
                if replace:
                    connection.execute(
                        text(
                            """
                            UPDATE app.questions
                            SET status=CAST(:status AS app.publication_status),
                                archived_at=NULL,
                                published_at=CASE
                                    WHEN :status='published'
                                    THEN COALESCE(published_at,now())
                                    ELSE NULL
                                END,
                                updated_by=:admin_id,
                                updated_at=now()
                            WHERE id=:id
                            """
                        ),
                        {
                            "id": question_id,
                            "status": status_value,
                            "admin_id": admin_id,
                        },
                    )
                counts.skipped += 1
            elif existing:
                question_id = existing["question_id"]
                version = existing["current_version"] + 1
                connection.execute(
                    text(
                        """
                        INSERT INTO app.question_versions (
                            question_id,version,prompt,answer_spec,explanation,
                            default_points,created_by
                        ) VALUES (
                            :id,:version,CAST(:prompt AS jsonb),CAST(:answer AS jsonb),
                            CAST(:explanation AS jsonb),1,:admin_id
                        )
                        """
                    ),
                    {
                        "id": question_id, "version": version,
                        "prompt": json.dumps(prompt, ensure_ascii=False),
                        "answer": json.dumps(item.answer_spec, ensure_ascii=False),
                        "explanation": json.dumps(item.explanation, ensure_ascii=False),
                        "admin_id": admin_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        UPDATE app.questions SET question_type_code=:type,
                            status=CAST(:status AS app.publication_status),
                            current_version=:version,updated_by=:admin_id,updated_at=now(),
                            published_at=CASE WHEN :status='published' THEN now() ELSE NULL END,
                            archived_at=NULL WHERE id=:id
                        """
                    ),
                    {
                        "id": question_id, "type": item.question_type,
                        "status": status_value, "version": version, "admin_id": admin_id,
                    },
                )
                counts.updated += 1
            else:
                question_id = uuid4()
                connection.execute(
                    text(
                        """
                        INSERT INTO app.questions (
                            id,subject_id,question_type_code,status,created_by,updated_by,
                            published_at
                        ) VALUES (
                            :id,:subject_id,:type,CAST(:status AS app.publication_status),
                            :admin_id,:admin_id,
                            CASE WHEN :status='published' THEN now() ELSE NULL END
                        )
                        """
                    ),
                    {
                        "id": question_id, "subject_id": subject_id,
                        "type": item.question_type, "status": status_value,
                        "admin_id": admin_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO app.question_versions (
                            question_id,version,prompt,answer_spec,explanation,
                            default_points,created_by
                        ) VALUES (
                            :id,1,CAST(:prompt AS jsonb),CAST(:answer AS jsonb),
                            CAST(:explanation AS jsonb),1,:admin_id
                        )
                        """
                    ),
                    {
                        "id": question_id,
                        "prompt": json.dumps(prompt, ensure_ascii=False),
                        "answer": json.dumps(item.answer_spec, ensure_ascii=False),
                        "explanation": json.dumps(item.explanation, ensure_ascii=False),
                        "admin_id": admin_id,
                    },
                )
                counts.inserted += 1
            connection.execute(
                text(
                    """
                    INSERT INTO app.history_question_metadata (
                        question_id,source_row_id,task_number,period,categories,stimulus_key,
                        review_required,review_reasons
                    ) VALUES (
                        :question_id,:source_id,:task_number,:period,
                        CAST(:categories AS jsonb),:stimulus_key,
                        :review_required,CAST(:review_reasons AS jsonb)
                    )
                    ON CONFLICT (source_row_id) DO UPDATE SET
                        question_id=EXCLUDED.question_id,
                        task_number=EXCLUDED.task_number,
                        period=EXCLUDED.period,
                        categories=EXCLUDED.categories,
                        stimulus_key=EXCLUDED.stimulus_key,
                        review_required=EXCLUDED.review_required,
                        review_reasons=EXCLUDED.review_reasons,
                        imported_at=now()
                    """
                ),
                {
                    "question_id": question_id, "source_id": item.source_id,
                    "task_number": item.task_number, "period": item.period,
                    "categories": json.dumps(item.categories, ensure_ascii=False),
                    "stimulus_key": item.stimulus_key,
                    "review_required": not item.publishable,
                    "review_reasons": json.dumps(
                        item.warnings or ("Требует проверки после импорта",),
                        ensure_ascii=False,
                    ),
                },
            )
        if replace:
            source_ids = [item.source_id for item in source.questions]
            stale_result = connection.execute(
                text(
                    """
                    SELECT metadata.question_id
                    FROM app.history_question_metadata AS metadata
                    WHERE NOT (
                        metadata.source_row_id = ANY(CAST(:source_ids AS integer[]))
                    )
                    """
                ),
                {"source_ids": source_ids},
            )
            stale_ids = list(stale_result.scalars())
            if stale_ids:
                connection.execute(
                    text(
                        """
                        UPDATE app.questions
                        SET status='archived',archived_at=now(),published_at=NULL,
                            updated_by=:admin_id,updated_at=now()
                        WHERE id = ANY(CAST(:question_ids AS uuid[]))
                        """
                    ),
                    {"question_ids": stale_ids, "admin_id": admin_id},
                )
                connection.execute(
                    text(
                        """
                        DELETE FROM app.history_question_metadata
                        WHERE question_id = ANY(CAST(:question_ids AS uuid[]))
                        """
                    ),
                    {"question_ids": stale_ids},
                )
                counts.removed = len(stale_ids)
        counts.images = connection.execute(
            text("SELECT count(*) FROM app.media_assets WHERE object_key LIKE 'history-trainer/%'")
        ).scalar_one()
        test_id = connection.execute(
            text(
                "SELECT id FROM app.tests "
                "WHERE subject_id=:subject_id AND slug='history-trainer'"
            ),
            {"subject_id": subject_id},
        ).scalar_one_or_none()
        blueprint = json.dumps({"kind": "history_trainer", "questionCount": 10})
        if test_id is None:
            connection.execute(
                text(
                    """
                    INSERT INTO app.tests (
                        subject_id,title,slug,description,mode,blueprint,status,
                        created_by,updated_by,published_at
                    ) VALUES (
                        :subject_id,'Тренажёр по истории','history-trainer',
                        'Конструктор тренировки по заданиям, периодам, картам и культуре.',
                        'generated',CAST(:blueprint AS jsonb),'published',
                        :admin_id,:admin_id,now()
                    )
                    """
                ),
                {"subject_id": subject_id, "blueprint": blueprint, "admin_id": admin_id},
            )
        else:
            connection.execute(
                text(
                    """
                    UPDATE app.tests SET mode='generated',blueprint=CAST(:blueprint AS jsonb),
                        status='published',published_at=COALESCE(published_at,now()),
                        archived_at=NULL,updated_by=:admin_id,updated_at=now()
                    WHERE id=:id
                    """
                ),
                {"id": test_id, "blueprint": blueprint, "admin_id": admin_id},
            )
    return counts
