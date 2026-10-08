from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import AdminPrincipal
from app.api.routes.catalog import Session, _offset, _page
from app.core.problems import ProblemException

router = APIRouter(prefix="/admin", tags=["Admin questions"])
SubjectCode = Literal["history", "society"]
QuestionType = Literal[
    "single_choice", "multiple_choice", "text", "matching", "ordering", "map", "graph",
    "self_check",
]
PublicationStatus = Literal["draft", "published", "archived"]

QUESTION_TEXT_MISSING = "Нет текста вопроса"
QUESTION_ANSWER_MISSING = "Нет ответа"
QUESTION_DUPLICATE = "Такой вопрос уже существует"
QUESTION_SIMILARITY_THRESHOLD = 0.9


class QuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: SubjectCode
    section_id: UUID | None = Field(default=None, alias="sectionId")
    test_id: UUID | None = Field(default=None, alias="testId")
    type: QuestionType
    prompt: dict[str, Any]
    answer_spec: dict[str, Any] = Field(alias="answerSpec")
    explanation: dict[str, Any] = Field(default_factory=dict)
    default_points: float = Field(default=1, alias="defaultPoints", gt=0)
    content_ids: list[UUID] = Field(default_factory=list, alias="contentIds")
    tag_ids: list[UUID] = Field(default_factory=list, alias="tagIds")
    allow_duplicate: bool = Field(default=False, alias="allowDuplicate")


class QuestionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    section_id: UUID | None = Field(default=None, alias="sectionId")
    type: QuestionType | None = None
    prompt: dict[str, Any] | None = None
    answer_spec: dict[str, Any] | None = Field(default=None, alias="answerSpec")
    explanation: dict[str, Any] | None = None
    default_points: float | None = Field(default=None, alias="defaultPoints", gt=0)
    content_ids: list[UUID] | None = Field(default=None, alias="contentIds")
    tag_ids: list[UUID] | None = Field(default=None, alias="tagIds")
    allow_duplicate: bool = Field(default=False, alias="allowDuplicate")


class TestQuestionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_id: UUID = Field(alias="questionId")
    position: int = Field(ge=1)
    points_override: float | None = Field(default=None, alias="pointsOverride", gt=0)
    required: bool = True


class GenerationBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_count: int = Field(alias="questionCount", ge=1, le=100)
    section_ids: list[UUID] = Field(default_factory=list, alias="sectionIds")
    question_types: list[QuestionType] = Field(default_factory=list, alias="questionTypes")
    tag_ids: list[UUID] = Field(default_factory=list, alias="tagIds")
    randomize_questions: bool = Field(default=True, alias="randomizeQuestions")


class TestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: SubjectCode
    section_id: UUID | None = Field(default=None, alias="sectionId")
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    mode: Literal["fixed", "generated"]
    time_limit_seconds: int | None = Field(default=None, alias="timeLimitSeconds", ge=1)
    questions: list[TestQuestionRef] = Field(default_factory=list)
    blueprint: GenerationBlueprint | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class TestPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    section_id: UUID | None = Field(default=None, alias="sectionId")
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    time_limit_seconds: int | None = Field(default=None, alias="timeLimitSeconds", ge=1)
    questions: list[TestQuestionRef] | None = None
    blueprint: GenerationBlueprint | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class QuestionErrorReportReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=2, max_length=3000)


def _etag(updated_at: Any) -> str:
    return f'W/"{updated_at.timestamp():.6f}"'


_RU_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
)


def _slugify_test_title(title: str) -> str:
    value = title.strip().lower().translate(_RU_TRANSLIT)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:80].rstrip("-") or "test"


def _normalized_question_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.split()).casefold().replace("ё", "е")


def _canonical_prompt_value(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, str):
        return _normalized_question_text(value)
    if isinstance(value, list):
        return [_canonical_prompt_value(item, parent_key) for item in value]
    if isinstance(value, dict):
        if parent_key == "image":
            checksum = value.get("checksumSha256")
            if isinstance(checksum, str) and checksum.strip():
                return {"checksumSha256": checksum.strip().lower()}
            url = value.get("url")
            if isinstance(url, str) and url.strip():
                return {"url": url.strip()}
        return {
            key: _canonical_prompt_value(item, key)
            for key, item in sorted(value.items())
            if key not in {"id", "createdAt"}
        }
    return value


def _question_fingerprint(question_type: str, prompt: dict[str, Any]) -> str:
    canonical = {
        "type": question_type,
        "prompt": _canonical_prompt_value(prompt),
    }
    serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _prompt_comparison_text(value: Any, parent_key: str | None = None) -> str:
    if parent_key == "image":
        return ""
    if isinstance(value, str):
        return _normalized_question_text(value)
    if isinstance(value, list):
        return " ".join(_prompt_comparison_text(item, parent_key) for item in value).strip()
    if isinstance(value, dict):
        return " ".join(
            _prompt_comparison_text(item, key)
            for key, item in sorted(value.items())
            if key not in {"id", "createdAt"}
        ).strip()
    return ""


def _prompt_image_signature(prompt: dict[str, Any]) -> str | None:
    image = prompt.get("image")
    if not isinstance(image, dict):
        return None
    checksum = image.get("checksumSha256")
    if isinstance(checksum, str) and checksum.strip():
        return f"sha256:{checksum.strip().lower()}"
    url = image.get("url")
    if isinstance(url, str) and url.strip():
        return f"url:{url.strip()}"
    return None


def _question_similarity(
    question_type: str,
    prompt: dict[str, Any],
    other_type: str,
    other_prompt: dict[str, Any],
) -> float:
    if _question_fingerprint(question_type, prompt) == _question_fingerprint(
        other_type, other_prompt
    ):
        return 1.0
    image_signature = _prompt_image_signature(prompt)
    other_image_signature = _prompt_image_signature(other_prompt)
    if image_signature != other_image_signature and (image_signature or other_image_signature):
        return 0.0
    current = _prompt_comparison_text(prompt)
    other = _prompt_comparison_text(other_prompt)
    if min(len(current), len(other)) < 20:
        return 0.0
    return SequenceMatcher(None, current, other, autojunk=False).ratio()


def _has_nonblank_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return bool(value) and all(_has_nonblank_value(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(
            _has_nonblank_value(key) and _has_nonblank_value(item)
            for key, item in value.items()
        )
    return True


def _answer_is_complete(
    question_type: str,
    prompt: dict[str, Any],
    answer_spec: dict[str, Any],
) -> bool:
    if question_type in {"single_choice", "multiple_choice"}:
        options = prompt.get("options")
        if not isinstance(options, list) or len(options) < 2:
            return False
        option_ids = {
            option.get("id")
            for option in options
            if isinstance(option, dict)
            and isinstance(option.get("id"), str)
            and option["id"].strip()
            and _has_nonblank_value(option.get("text"))
        }
        if len(option_ids) != len(options):
            return False
        if question_type == "single_choice":
            correct_id = answer_spec.get("correctOptionId")
            return (
                isinstance(correct_id, str)
                and bool(correct_id.strip())
                and correct_id in option_ids
            )
        correct_ids = answer_spec.get("correctOptionIds")
        return (
            isinstance(correct_ids, list)
            and bool(correct_ids)
            and all(isinstance(item, str) and item.strip() for item in correct_ids)
            and len(set(correct_ids)) == len(correct_ids)
            and set(correct_ids).issubset(option_ids)
        )
    if question_type == "text":
        answers = answer_spec.get("acceptedAnswers")
        return isinstance(answers, list) and bool(answers) and all(
            _has_nonblank_value(answer) for answer in answers
        )
    if question_type == "matching":
        left_items = prompt.get("leftItems")
        right_items = prompt.get("rightItems")
        pairs = answer_spec.get("pairs")
        item_groups = [left_items, right_items]
        if not all(isinstance(items, list) and len(items) >= 2 for items in item_groups):
            return False
        if not isinstance(pairs, dict) or len(pairs) != len(left_items):
            return False
        left_ids = {
            item.get("id")
            for item in left_items
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"].strip()
            and _has_nonblank_value(item.get("text"))
        }
        right_ids = {
            item.get("id")
            for item in right_items
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"].strip()
            and _has_nonblank_value(item.get("text"))
        }
        return (
            len(left_ids) == len(left_items)
            and len(right_ids) == len(right_items)
            and all(
                isinstance(key, str)
                and key.strip()
                and isinstance(value, str)
                and value.strip()
                for key, value in pairs.items()
            )
            and set(pairs) == left_ids
            and set(pairs.values()).issubset(right_ids)
        )
    if question_type == "ordering":
        items = prompt.get("items")
        order = answer_spec.get("correctOrder")
        if not isinstance(items, list) or len(items) < 2 or not isinstance(order, list):
            return False
        item_ids = {
            item.get("id")
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"].strip()
            and _has_nonblank_value(item.get("text"))
        }
        return (
            len(item_ids) == len(items)
            and len(order) == len(items)
            and all(isinstance(item, str) and item.strip() for item in order)
            and len(set(order)) == len(order)
            and set(order) == item_ids
        )
    if question_type == "self_check":
        model_answer = answer_spec.get("modelAnswer")
        return _has_nonblank_value(model_answer)
    return _has_nonblank_value(answer_spec.get("correctResponse"))


def _validate_question_content(
    question_type: str,
    prompt: dict[str, Any],
    answer_spec: dict[str, Any],
) -> None:
    errors: list[dict[str, str]] = []
    if not _normalized_question_text(prompt.get("text")):
        errors.append({"field": "prompt.text", "code": "missing", "message": QUESTION_TEXT_MISSING})
    if not _answer_is_complete(question_type, prompt, answer_spec):
        errors.append(
            {"field": "answerSpec", "code": "missing", "message": QUESTION_ANSWER_MISSING}
        )
    if errors:
        raise ProblemException(
            400,
            "incomplete_question",
            "Вопрос заполнен не полностью",
            "; ".join(error["message"] for error in errors),
            errors,
        )


async def _unique_test_slug(session: Session, title: str) -> str:
    base = _slugify_test_title(title)
    result = await session.execute(
        text("SELECT slug FROM app.tests WHERE slug = :base OR slug LIKE :pattern"),
        {"base": base, "pattern": f"{base}-%"},
    )
    existing = set(result.scalars())
    if base not in existing:
        return base
    suffix = 2
    while f"{base[:75]}-{suffix}" in existing:
        suffix += 1
    return f"{base[:75]}-{suffix}"


def _check_etag(if_match: str, updated_at: Any) -> None:
    if if_match != _etag(updated_at):
        raise ProblemException(
            412,
            "etag_mismatch",
            "Resource was changed",
            "Обновите данные и повторите операцию",
        )


async def _subject_id(session: Session, code: str, section_id: UUID | None) -> int:
    result = await session.execute(
        text("SELECT id FROM app.subjects WHERE code = :code"), {"code": code}
    )
    subject_id = result.scalar_one_or_none()
    if subject_id is None:
        raise ProblemException(400, "invalid_subject", "Unknown subject")
    if section_id is not None:
        section = await session.execute(
            text(
                """
                SELECT 1 FROM app.sections
                WHERE id = :section_id AND subject_id = :subject_id
                """
            ),
            {"section_id": section_id, "subject_id": subject_id},
        )
        if section.scalar_one_or_none() is None:
            raise ProblemException(400, "invalid_section", "Section belongs to another subject")
    return subject_id


async def _question_test_ids(session: Session, question_id: UUID) -> list[UUID]:
    result = await session.execute(
        text(
            "SELECT test_id FROM app.test_questions "
            "WHERE question_id = :question_id ORDER BY test_id"
        ),
        {"question_id": question_id},
    )
    return list(result.scalars())


async def _ensure_question_unique(
    session: Session,
    *,
    subject_id: int,
    question_type: str,
    prompt: dict[str, Any],
    test_ids: list[UUID] | None = None,
    exclude_question_id: UUID | None = None,
) -> None:
    del test_ids
    fingerprint = _question_fingerprint(question_type, prompt)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:fingerprint, 0))"),
        {"fingerprint": fingerprint},
    )
    parameters: dict[str, Any] = {
        "subject_id": subject_id,
    }
    conditions = [
        "question.subject_id = :subject_id",
    ]
    if exclude_question_id is not None:
        conditions.append("question.id <> :exclude_question_id")
        parameters["exclude_question_id"] = exclude_question_id
    result = await session.execute(
        text(
            f"""
            SELECT DISTINCT question.id, question.question_type_code, version.prompt
            FROM app.questions AS question
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE {' AND '.join(conditions)}
            """
        ),
        parameters,
    )
    for row in result.mappings():
        similarity = _question_similarity(
            question_type,
            prompt,
            row["question_type_code"],
            row["prompt"],
        )
        if similarity >= QUESTION_SIMILARITY_THRESHOLD:
            raise ProblemException(
                409,
                "duplicate_question",
                QUESTION_DUPLICATE,
                f"Найден похожий вопрос: совпадение {round(similarity * 100)}%",
                [
                    {
                        "field": "prompt",
                        "code": "duplicate",
                        "message": QUESTION_DUPLICATE,
                        "questionId": str(row["id"]),
                        "similarity": round(similarity, 4),
                    }
                ],
            )


def _question_summary(row: Any) -> dict[str, Any]:
    prompt = row.get("prompt") or {}
    prompt_preview = prompt.get("text") if isinstance(prompt, dict) else None
    return {
        "id": row["id"],
        "subject": row["subject"],
        "sectionId": row["section_id"],
        "type": row["type"],
        "status": row["status"],
        "currentVersion": row["current_version"],
        "promptPreview": prompt_preview,
        "testCount": int(row.get("test_count") or 0),
        "updatedAt": row["updated_at"],
    }


async def _question_detail(session: Session, question_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT question.id, subject.code AS subject, question.section_id,
                   question.question_type_code AS type,
                   question.status::text AS status, question.current_version,
                   question.created_at, question.updated_at, question.published_at,
                   question.archived_at, version.prompt, version.answer_spec,
                   version.explanation, version.default_points,
                   (SELECT count(*) FROM app.test_questions linked
                    WHERE linked.question_id = question.id)::integer AS test_count
            FROM app.questions AS question
            JOIN app.subjects AS subject ON subject.id = question.subject_id
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE question.id = :question_id
            """
        ),
        {"question_id": question_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    content_result = await session.execute(
        text(
            "SELECT content_id FROM app.question_content_links "
            "WHERE question_id = :id ORDER BY content_id"
        ),
        {"id": question_id},
    )
    tag_result = await session.execute(
        text("SELECT tag_id FROM app.question_tags WHERE question_id = :id ORDER BY tag_id"),
        {"id": question_id},
    )
    return {
        **_question_summary(row),
        "prompt": row["prompt"],
        "answerSpec": row["answer_spec"],
        "explanation": row["explanation"],
        "defaultPoints": float(row["default_points"]),
        "contentIds": list(content_result.scalars()),
        "tagIds": list(tag_result.scalars()),
        "createdAt": row["created_at"],
        "publishedAt": row["published_at"],
        "archivedAt": row["archived_at"],
    }


async def _replace_question_links(
    session: Session,
    question_id: UUID,
    content_ids: list[UUID] | None,
    tag_ids: list[UUID] | None,
) -> None:
    if content_ids is not None:
        await session.execute(
            text("DELETE FROM app.question_content_links WHERE question_id = :id"),
            {"id": question_id},
        )
        for content_id in dict.fromkeys(content_ids):
            await session.execute(
                text(
                    "INSERT INTO app.question_content_links (question_id, content_id) "
                    "VALUES (:question_id, :content_id)"
                ),
                {"question_id": question_id, "content_id": content_id},
            )
    if tag_ids is not None:
        await session.execute(
            text("DELETE FROM app.question_tags WHERE question_id = :id"), {"id": question_id}
        )
        for tag_id in dict.fromkeys(tag_ids):
            await session.execute(
                text(
                    "INSERT INTO app.question_tags (question_id, tag_id) "
                    "VALUES (:question_id, :tag_id)"
                ),
                {"question_id": question_id, "tag_id": tag_id},
            )


@router.get("/questions", operation_id="adminListQuestions", include_in_schema=False)
async def admin_list_questions(
    admin: AdminPrincipal,
    session: Session,
    subject: SubjectCode | None = None,
    section_id: Annotated[UUID | None, Query(alias="sectionId")] = None,
    question_type: Annotated[QuestionType | None, Query(alias="type")] = None,
    publication_status: Annotated[PublicationStatus | None, Query(alias="status")] = None,
    assignment: Literal["all", "assigned", "unassigned"] = "all",
    search: Annotated[str | None, Query(max_length=300)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    del admin
    offset = _offset(cursor)
    conditions = ["true"]
    parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
    for value, condition, key in [
        (subject, "subject.code = :subject", "subject"),
        (section_id, "question.section_id = :section_id", "section_id"),
        (question_type, "question.question_type_code = :question_type", "question_type"),
        (publication_status, "question.status = CAST(:status AS app.publication_status)", "status"),
    ]:
        if value is not None:
            conditions.append(condition)
            parameters[key] = value
    if assignment == "assigned":
        conditions.append(
            "EXISTS (SELECT 1 FROM app.test_questions assigned "
            "WHERE assigned.question_id = question.id)"
        )
    elif assignment == "unassigned":
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM app.test_questions assigned "
            "WHERE assigned.question_id = question.id)"
        )
    if search and search.strip():
        conditions.append("version.prompt->>'text' ILIKE :search")
        parameters["search"] = f"%{search.strip()}%"
    result = await session.execute(
        text(
            f"""
            SELECT question.id, subject.code AS subject, question.section_id,
                   question.question_type_code AS type,
                   question.status::text AS status, question.current_version,
                   question.updated_at, version.prompt,
                   (SELECT count(*) FROM app.test_questions linked
                    WHERE linked.question_id = question.id)::integer AS test_count
            FROM app.questions AS question
            JOIN app.subjects AS subject ON subject.id = question.subject_id
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE {' AND '.join(conditions)}
            ORDER BY question.updated_at DESC, question.id
            OFFSET :offset LIMIT :fetch_limit
            """
        ),
        parameters,
    )
    rows = list(result.mappings())
    return {
        "items": [_question_summary(row) for row in rows[:limit]],
        "page": _page(offset, limit, len(rows) > limit),
    }


@router.post(
    "/questions",
    status_code=status.HTTP_201_CREATED,
    operation_id="adminCreateQuestion",
    include_in_schema=False,
)
async def admin_create_question(
    payload: QuestionCreate,
    admin: AdminPrincipal,
    session: Session,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    _validate_question_content(payload.type, payload.prompt, payload.answer_spec)
    subject_id = await _subject_id(session, payload.subject, payload.section_id)
    if not payload.allow_duplicate:
        await _ensure_question_unique(
            session,
            subject_id=subject_id,
            question_type=payload.type,
            prompt=payload.prompt,
            test_ids=[payload.test_id] if payload.test_id is not None else None,
        )
    question_id = uuid4()
    try:
        await session.execute(
            text(
                """
                INSERT INTO app.questions (
                    id, subject_id, section_id, question_type_code,
                    created_by, updated_by
                ) VALUES (
                    :id, :subject_id, :section_id, :type, :admin_id, :admin_id
                )
                """
            ),
            {
                "id": question_id,
                "subject_id": subject_id,
                "section_id": payload.section_id,
                "type": payload.type,
                "admin_id": admin.user_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO app.question_versions (
                    question_id, version, prompt, answer_spec, explanation,
                    default_points, created_by
                ) VALUES (
                    :id, 1, CAST(:prompt AS jsonb), CAST(:answer AS jsonb),
                    CAST(:explanation AS jsonb), :points, :admin_id
                )
                """
            ),
            {
                "id": question_id,
                "prompt": json.dumps(payload.prompt, ensure_ascii=False),
                "answer": json.dumps(payload.answer_spec, ensure_ascii=False),
                "explanation": json.dumps(payload.explanation, ensure_ascii=False),
                "points": payload.default_points,
                "admin_id": admin.user_id,
            },
        )
        await _replace_question_links(
            session, question_id, payload.content_ids, payload.tag_ids
        )
        if payload.test_id is not None:
            test_result = await session.execute(
                text(
                    """
                    SELECT test.id, subject.code AS subject, test.mode::text AS mode
                    FROM app.tests AS test
                    JOIN app.subjects AS subject ON subject.id = test.subject_id
                    WHERE test.id = :test_id
                    """
                ),
                {"test_id": payload.test_id},
            )
            test_row = test_result.mappings().one_or_none()
            if test_row is None:
                raise ProblemException(404, "test_not_found", "Test not found")
            if test_row["subject"] != payload.subject or test_row["mode"] != "fixed":
                raise ProblemException(
                    400,
                    "invalid_test_question",
                    "Question and fixed test must have the same subject",
                )
            position_result = await session.execute(
                text(
                    "SELECT COALESCE(max(position), 0) + 1 "
                    "FROM app.test_questions WHERE test_id = :test_id"
                ),
                {"test_id": payload.test_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO app.test_questions (test_id, question_id, position)
                    VALUES (:test_id, :question_id, :position)
                    """
                ),
                {
                    "test_id": payload.test_id,
                    "question_id": question_id,
                    "position": position_result.scalar_one(),
                },
            )
            await session.execute(
                text(
                    """
                    UPDATE app.tests
                    SET status = CASE
                            WHEN status = 'published' THEN 'draft'::app.publication_status
                            ELSE status
                        END,
                        published_at = CASE
                            WHEN status = 'published' THEN NULL
                            ELSE published_at
                        END,
                        updated_at = now()
                    WHERE id = :test_id
                    """
                ),
                {"test_id": payload.test_id},
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(
            400,
            "invalid_question_links",
            "A linked material, tag, or section does not exist",
        ) from exc
    detail = await _question_detail(session, question_id)
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


@router.get(
    "/questions/{question_id}", operation_id="adminGetQuestion", include_in_schema=False
)
async def admin_get_question(
    question_id: UUID, admin: AdminPrincipal, session: Session, response: Response
) -> dict[str, object]:
    del admin
    detail = await _question_detail(session, question_id)
    if detail is None:
        raise ProblemException(404, "question_not_found", "Question not found")
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


@router.patch(
    "/questions/{question_id}",
    operation_id="adminCreateQuestionVersion",
    include_in_schema=False,
)
async def admin_update_question(
    question_id: UUID,
    payload: QuestionPatch,
    admin: AdminPrincipal,
    session: Session,
    response: Response,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> dict[str, object]:
    if not payload.model_fields_set:
        raise ProblemException(400, "empty_update", "No fields supplied")
    current = await _question_detail(session, question_id)
    if current is None:
        raise ProblemException(404, "question_not_found", "Question not found")
    _check_etag(if_match, current["updatedAt"])
    new_version = current["currentVersion"] + 1
    prompt = payload.prompt if payload.prompt is not None else current["prompt"]
    answer = payload.answer_spec if payload.answer_spec is not None else current["answerSpec"]
    explanation = (
        payload.explanation if payload.explanation is not None else current["explanation"]
    )
    points = payload.default_points or current["defaultPoints"]
    question_type = payload.type or current["type"]
    _validate_question_content(question_type, prompt, answer)
    section_id = (
        payload.section_id
        if "section_id" in payload.model_fields_set
        else current["sectionId"]
    )
    subject_id = await _subject_id(session, current["subject"], section_id)
    test_ids = await _question_test_ids(session, question_id)
    if not payload.allow_duplicate:
        await _ensure_question_unique(
            session,
            subject_id=subject_id,
            question_type=question_type,
            prompt=prompt,
            test_ids=test_ids or None,
            exclude_question_id=question_id,
        )
    try:
        await session.execute(
            text(
                """
                INSERT INTO app.question_versions (
                    question_id, version, prompt, answer_spec, explanation,
                    default_points, created_by
                ) VALUES (
                    :id, :version, CAST(:prompt AS jsonb), CAST(:answer AS jsonb),
                    CAST(:explanation AS jsonb), :points, :admin_id
                )
                """
            ),
            {
                "id": question_id,
                "version": new_version,
                "prompt": json.dumps(prompt, ensure_ascii=False),
                "answer": json.dumps(answer, ensure_ascii=False),
                "explanation": json.dumps(explanation, ensure_ascii=False),
                "points": points,
                "admin_id": admin.user_id,
            },
        )
        await session.execute(
            text(
                """
                UPDATE app.questions
                SET section_id = :section_id,
                    question_type_code = :type,
                    current_version = :version,
                    updated_by = :admin_id,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": question_id,
                "section_id": section_id,
                "type": question_type,
                "version": new_version,
                "admin_id": admin.user_id,
            },
        )
        await _replace_question_links(
            session, question_id, payload.content_ids, payload.tag_ids
        )
        await session.execute(
            text(
                """
                UPDATE app.history_question_metadata
                SET review_required = false, review_reasons = '[]'::jsonb
                WHERE question_id = :question_id
                """
            ),
            {"question_id": question_id},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(
            400,
            "invalid_question_links",
            "A linked material, tag, or section does not exist",
        ) from exc
    detail = await _question_detail(session, question_id)
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


async def _change_question_status(
    question_id: UUID,
    target: Literal["published", "archived"],
    admin: AdminPrincipal,
    session: Session,
    if_match: str,
) -> dict[str, Any]:
    current = await _question_detail(session, question_id)
    if current is None:
        raise ProblemException(404, "question_not_found", "Question not found")
    _check_etag(if_match, current["updatedAt"])
    if target == "published" and (not current["prompt"] or not current["answerSpec"]):
        raise ProblemException(422, "question_incomplete", "Question is incomplete")
    await session.execute(
        text(
            """
            UPDATE app.questions
            SET status = CAST(:status AS app.publication_status),
                published_at = CASE WHEN :status = 'published' THEN now() ELSE published_at END,
                archived_at = CASE WHEN :status = 'archived' THEN now() ELSE NULL END,
                updated_by = :admin_id, updated_at = now()
            WHERE id = :id
            """
        ),
        {"status": target, "admin_id": admin.user_id, "id": question_id},
    )
    if target == "archived":
        await session.execute(
            text(
                """
                UPDATE app.tests AS test
                SET status = 'draft', published_at = NULL,
                    updated_by = :admin_id, updated_at = now()
                WHERE test.status = 'published'
                  AND EXISTS (
                      SELECT 1 FROM app.test_questions AS tq
                      WHERE tq.test_id = test.id AND tq.question_id = :question_id
                  )
                """
            ),
            {"admin_id": admin.user_id, "question_id": question_id},
        )
    elif target == "published":
        await session.execute(
            text(
                """
                UPDATE app.history_question_metadata
                SET review_required = false, review_reasons = '[]'::jsonb
                WHERE question_id = :question_id
                """
            ),
            {"question_id": question_id},
        )
    await session.commit()
    return await _question_detail(session, question_id)


@router.post(
    "/questions/{question_id}/publish",
    operation_id="adminPublishQuestion",
    include_in_schema=False,
)
async def admin_publish_question(
    question_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    return await _change_question_status(question_id, "published", admin, session, if_match)


@router.post(
    "/questions/{question_id}/archive",
    operation_id="adminArchiveQuestion",
    include_in_schema=False,
)
async def admin_archive_question(
    question_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    return await _change_question_status(question_id, "archived", admin, session, if_match)


async def _test_detail(session: Session, test_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT test.id, subject.code AS subject, test.section_id, test.title,
                   test.slug, test.description, test.mode::text AS mode,
                   test.blueprint, test.time_limit_seconds,
                   section.parent_id AS section_parent_id,
                   section.code AS section_code, section.title AS section_title,
                   section.description AS section_description,
                   section.sort_order AS section_sort_order,
                   test.status::text AS status, test.created_at, test.updated_at,
                   test.published_at, test.archived_at,
                   count(tq.question_id)::integer AS question_count,
                   COALESCE(sum(COALESCE(tq.points_override, version.default_points)), 0)
                       AS total_points
            FROM app.tests AS test
            JOIN app.subjects AS subject ON subject.id = test.subject_id
            LEFT JOIN app.sections AS section ON section.id = test.section_id
            LEFT JOIN app.test_questions AS tq ON tq.test_id = test.id
            LEFT JOIN app.questions AS question ON question.id = tq.question_id
            LEFT JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE test.id = :test_id
            GROUP BY test.id, subject.code, section.id
            """
        ),
        {"test_id": test_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    questions_result = await session.execute(
        text(
            """
            SELECT question_id, position, points_override, is_required
            FROM app.test_questions
            WHERE test_id = :test_id
            ORDER BY position
            """
        ),
        {"test_id": test_id},
    )
    return {
        "id": row["id"],
        "subject": row["subject"],
        "section": (
            {
                "id": row["section_id"],
                "subject": row["subject"],
                "parentId": row["section_parent_id"],
                "code": row["section_code"],
                "title": row["section_title"],
                "description": row["section_description"],
                "sortOrder": row["section_sort_order"],
            }
            if row["section_id"] is not None
            else None
        ),
        "title": row["title"],
        "slug": row["slug"],
        "description": row["description"],
        "mode": row["mode"],
        "questionCount": row["question_count"],
        "timeLimitSeconds": row["time_limit_seconds"],
        "totalPoints": float(row["total_points"]),
        "status": row["status"],
        "questions": [
            {
                "questionId": question["question_id"],
                "position": question["position"],
                "pointsOverride": (
                    float(question["points_override"])
                    if question["points_override"] is not None
                    else None
                ),
                "required": question["is_required"],
            }
            for question in questions_result.mappings()
        ],
        "blueprint": row["blueprint"] or None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "publishedAt": row["published_at"],
        "archivedAt": row["archived_at"],
    }


def _validate_question_refs(questions: list[TestQuestionRef]) -> None:
    ids = [item.question_id for item in questions]
    positions = [item.position for item in questions]
    if len(ids) != len(set(ids)):
        raise ProblemException(400, "duplicate_question", "Question is repeated in test")
    if len(positions) != len(set(positions)):
        raise ProblemException(400, "duplicate_position", "Question positions must be unique")


async def _replace_test_questions(
    session: Session,
    test_id: UUID,
    subject_code: str,
    questions: list[TestQuestionRef],
    *,
    require_published: bool = False,
) -> None:
    _validate_question_refs(questions)
    if require_published and not questions:
        raise ProblemException(422, "test_has_no_questions", "Published test has no questions")
    if questions:
        id_parameters = {
            f"question_id_{index}": item.question_id
            for index, item in enumerate(questions)
        }
        placeholders = ", ".join(f":{key}" for key in id_parameters)
        status_condition = "AND question.status = 'published'" if require_published else ""
        result = await session.execute(
            text(
                f"""
                SELECT count(*)
                FROM app.questions AS question
                JOIN app.subjects AS subject ON subject.id = question.subject_id
                WHERE question.id IN ({placeholders})
                  AND subject.code = :subject
                  {status_condition}
                """
            ),
            {**id_parameters, "subject": subject_code},
        )
        if result.scalar_one() != len(questions):
            title = (
                "Published tests require published questions from the same subject"
                if require_published
                else "Questions must exist in the same subject"
            )
            raise ProblemException(
                400, "invalid_test_questions", title
            )
    await session.execute(
        text("DELETE FROM app.test_questions WHERE test_id = :test_id"),
        {"test_id": test_id},
    )
    for item in questions:
        await session.execute(
            text(
                """
                INSERT INTO app.test_questions (
                    test_id, question_id, position, points_override, is_required
                ) VALUES (
                    :test_id, :question_id, :position, :points, :required
                )
                """
            ),
            {
                "test_id": test_id,
                "question_id": item.question_id,
                "position": item.position,
                "points": item.points_override,
                "required": item.required,
            },
        )


@router.get("/tests", operation_id="adminListTests", include_in_schema=False)
async def admin_list_tests(
    admin: AdminPrincipal,
    session: Session,
    subject: SubjectCode | None = None,
    section_id: Annotated[UUID | None, Query(alias="sectionId")] = None,
    publication_status: Annotated[PublicationStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    del admin
    offset = _offset(cursor)
    conditions = ["true"]
    parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
    if subject is not None:
        conditions.append("subject.code = :subject")
        parameters["subject"] = subject
    if section_id is not None:
        conditions.append("test.section_id = :section_id")
        parameters["section_id"] = section_id
    if publication_status is not None:
        conditions.append("test.status = CAST(:status AS app.publication_status)")
        parameters["status"] = publication_status
    result = await session.execute(
        text(
            f"""
            SELECT test.id
            FROM app.tests AS test
            JOIN app.subjects AS subject ON subject.id = test.subject_id
            WHERE {' AND '.join(conditions)}
            ORDER BY test.updated_at DESC, test.id
            OFFSET :offset LIMIT :fetch_limit
            """
        ),
        parameters,
    )
    ids = list(result.scalars())
    items = [await _test_detail(session, test_id) for test_id in ids[:limit]]
    return {"items": items, "page": _page(offset, limit, len(ids) > limit)}


@router.get(
    "/tests/{test_id}/questions",
    operation_id="adminListTestQuestions",
    include_in_schema=False,
)
async def admin_list_test_questions(
    test_id: UUID,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, object]:
    del admin
    exists = await session.execute(
        text("SELECT 1 FROM app.tests WHERE id = :test_id"), {"test_id": test_id}
    )
    if exists.scalar_one_or_none() is None:
        raise ProblemException(404, "test_not_found", "Test not found")
    result = await session.execute(
        text(
            """
            SELECT question.id, subject.code AS subject, question.section_id,
                   question.question_type_code AS type,
                   question.status::text AS status, question.current_version,
                   question.updated_at, version.prompt, version.default_points,
                   tq.position, tq.points_override, tq.is_required
            FROM app.test_questions AS tq
            JOIN app.questions AS question ON question.id = tq.question_id
            JOIN app.subjects AS subject ON subject.id = question.subject_id
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE tq.test_id = :test_id
            ORDER BY tq.position, question.id
            """
        ),
        {"test_id": test_id},
    )
    items: list[dict[str, Any]] = []
    for row in result.mappings():
        item = _question_summary(row)
        item.update(
            {
                "position": row["position"],
                "points": float(row["points_override"] or row["default_points"]),
                "pointsOverride": (
                    float(row["points_override"])
                    if row["points_override"] is not None
                    else None
                ),
                "required": row["is_required"],
                "image": (row["prompt"] or {}).get("image"),
            }
        )
        items.append(item)
    return {"items": items}


@router.post(
    "/tests",
    status_code=status.HTTP_201_CREATED,
    operation_id="adminCreateTest",
    include_in_schema=False,
)
async def admin_create_test(
    payload: TestCreate,
    admin: AdminPrincipal,
    session: Session,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    subject_id = await _subject_id(session, payload.subject, payload.section_id)
    if payload.mode == "generated" and not payload.blueprint:
        raise ProblemException(400, "blueprint_required", "Generated test needs blueprint")
    test_id = uuid4()
    slug = await _unique_test_slug(session, payload.title)
    try:
        await session.execute(
            text(
                """
                INSERT INTO app.tests (
                    id, subject_id, section_id, title, slug, description, mode,
                    blueprint, time_limit_seconds, created_by, updated_by
                ) VALUES (
                    :id, :subject_id, :section_id, :title, :slug, :description,
                    CAST(:mode AS app.test_mode), CAST(:blueprint AS jsonb),
                    :time_limit, :admin_id, :admin_id
                )
                """
            ),
            {
                "id": test_id,
                "subject_id": subject_id,
                "section_id": payload.section_id,
                "title": payload.title.strip(),
                "slug": slug,
                "description": payload.description,
                "mode": payload.mode,
                "blueprint": json.dumps(
                    payload.blueprint.model_dump(mode="json", by_alias=True)
                    if payload.blueprint
                    else {},
                    ensure_ascii=False,
                ),
                "time_limit": payload.time_limit_seconds,
                "admin_id": admin.user_id,
            },
        )
        if payload.mode == "fixed":
            await _replace_test_questions(
                session, test_id, payload.subject, payload.questions
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(409, "test_conflict", "Test slug already exists") from exc
    detail = await _test_detail(session, test_id)
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


@router.get("/tests/{test_id}", operation_id="adminGetTest", include_in_schema=False)
async def admin_get_test(
    test_id: UUID, admin: AdminPrincipal, session: Session, response: Response
) -> dict[str, object]:
    del admin
    detail = await _test_detail(session, test_id)
    if detail is None:
        raise ProblemException(404, "test_not_found", "Test not found")
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


@router.patch(
    "/tests/{test_id}", operation_id="adminUpdateTest", include_in_schema=False
)
async def admin_update_test(
    test_id: UUID,
    payload: TestPatch,
    admin: AdminPrincipal,
    session: Session,
    response: Response,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> dict[str, object]:
    if not payload.model_fields_set:
        raise ProblemException(400, "empty_update", "No fields supplied")
    current = await _test_detail(session, test_id)
    if current is None:
        raise ProblemException(404, "test_not_found", "Test not found")
    _check_etag(if_match, current["updatedAt"])
    if "section_id" in payload.model_fields_set:
        await _subject_id(session, current["subject"], payload.section_id)
    values = {
        "id": test_id,
        "section_id": payload.section_id
        if "section_id" in payload.model_fields_set
        else (current["section"]["id"] if current["section"] else None),
        "title": payload.title or current["title"],
        "slug": current["slug"],
        "description": payload.description
        if "description" in payload.model_fields_set
        else current["description"],
        "time_limit": payload.time_limit_seconds
        if "time_limit_seconds" in payload.model_fields_set
        else current["timeLimitSeconds"],
        "blueprint": json.dumps(
            (
                payload.blueprint.model_dump(mode="json", by_alias=True)
                if payload.blueprint is not None
                else {}
            )
            if "blueprint" in payload.model_fields_set
            else current["blueprint"] or {},
            ensure_ascii=False,
        ),
        "admin_id": admin.user_id,
    }
    try:
        await session.execute(
            text(
                """
                UPDATE app.tests
                SET section_id = :section_id, title = :title, slug = :slug,
                    description = :description, time_limit_seconds = :time_limit,
                    blueprint = CAST(:blueprint AS jsonb), updated_by = :admin_id,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            values,
        )
        if payload.questions is not None:
            if current["mode"] != "fixed":
                raise ProblemException(
                    400, "fixed_questions_forbidden", "Generated test cannot have fixed questions"
                )
            await _replace_test_questions(
                session,
                test_id,
                current["subject"],
                payload.questions,
                require_published=current["status"] == "published",
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(409, "test_conflict", "Test slug already exists") from exc
    detail = await _test_detail(session, test_id)
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


async def _change_test_status(
    test_id: UUID,
    target: Literal["published", "archived"],
    admin: AdminPrincipal,
    session: Session,
    if_match: str,
) -> dict[str, Any]:
    current = await _test_detail(session, test_id)
    if current is None:
        raise ProblemException(404, "test_not_found", "Test not found")
    _check_etag(if_match, current["updatedAt"])
    if target == "published":
        if current["mode"] == "fixed" and not current["questions"]:
            raise ProblemException(422, "test_has_no_questions", "Test has no questions")
        if current["mode"] == "generated":
            raise ProblemException(
                422,
                "generated_test_unsupported",
                "Generated tests are not enabled yet",
            )
        unpublished = await session.execute(
            text(
                """
                SELECT count(*)
                FROM app.test_questions AS tq
                JOIN app.questions AS question ON question.id = tq.question_id
                WHERE tq.test_id = :test_id AND question.status <> 'published'
                """
            ),
            {"test_id": test_id},
        )
        if unpublished.scalar_one() > 0:
            raise ProblemException(
                422, "test_has_unpublished_questions", "Publish every question first"
            )
    await session.execute(
        text(
            """
            UPDATE app.tests
            SET status = CAST(:status AS app.publication_status),
                published_at = CASE WHEN :status = 'published' THEN now() ELSE published_at END,
                archived_at = CASE WHEN :status = 'archived' THEN now() ELSE NULL END,
                updated_by = :admin_id, updated_at = now()
            WHERE id = :id
            """
        ),
        {"status": target, "admin_id": admin.user_id, "id": test_id},
    )
    await session.commit()
    return await _test_detail(session, test_id)


@router.post(
    "/tests/{test_id}/publish",
    operation_id="adminPublishTest",
    include_in_schema=False,
)
async def admin_publish_test(
    test_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    return await _change_test_status(test_id, "published", admin, session, if_match)


@router.post(
    "/tests/{test_id}/archive",
    operation_id="adminArchiveTest",
    include_in_schema=False,
)
async def admin_archive_test(
    test_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    return await _change_test_status(test_id, "archived", admin, session, if_match)


@router.get(
    "/history-trainer/questions",
    operation_id="adminListHistoryTrainerQuestions",
    include_in_schema=False,
)
async def admin_list_history_trainer_questions(
    admin: AdminPrincipal,
    session: Session,
    task_number: Annotated[int | None, Query(alias="taskNumber", ge=1, le=21)] = None,
    review: Literal["all", "required", "ready"] = "all",
    publication_status: Annotated[PublicationStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=300)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    del admin
    offset = _offset(cursor)
    conditions = ["true"]
    parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
    if task_number is not None:
        conditions.append("metadata.task_number = :task_number")
        parameters["task_number"] = task_number
    if review == "required":
        conditions.append("metadata.review_required = true")
    elif review == "ready":
        conditions.append("metadata.review_required = false")
    if publication_status is not None:
        conditions.append("question.status = CAST(:status AS app.publication_status)")
        parameters["status"] = publication_status
    if search and search.strip():
        conditions.append("version.prompt->>'text' ILIKE :search")
        parameters["search"] = f"%{search.strip()}%"
    result = await session.execute(
        text(
            f"""
            SELECT question.id, question.question_type_code AS type,
                   question.status::text AS status, question.current_version,
                   question.updated_at, version.prompt, version.default_points,
                   metadata.source_row_id, metadata.task_number, metadata.period,
                   metadata.categories, metadata.review_required,
                   metadata.review_reasons
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            JOIN app.question_versions AS version
              ON version.question_id = question.id
             AND version.version = question.current_version
            WHERE {' AND '.join(conditions)}
            ORDER BY metadata.review_required DESC, metadata.task_number,
                     metadata.source_row_id
            OFFSET :offset LIMIT :fetch_limit
            """
        ),
        parameters,
    )
    rows = list(result.mappings())
    items = []
    for row in rows[:limit]:
        prompt = row["prompt"] or {}
        items.append(
            {
                "id": row["id"],
                "sourceRowId": row["source_row_id"],
                "taskNumber": row["task_number"],
                "period": row["period"],
                "categories": row["categories"] or [],
                "type": row["type"],
                "status": row["status"],
                "currentVersion": row["current_version"],
                "defaultPoints": float(row["default_points"]),
                "promptPreview": prompt.get("text"),
                "image": prompt.get("image"),
                "reviewRequired": row["review_required"],
                "reviewReasons": row["review_reasons"] or [],
                "updatedAt": row["updated_at"],
            }
        )
    return {
        "items": items,
        "page": _page(offset, limit, len(rows) > limit),
    }


@router.get(
    "/history-trainer/status",
    operation_id="adminGetHistoryTrainerStatus",
    include_in_schema=False,
)
async def admin_history_trainer_status(
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, object]:
    del admin
    summary = await session.execute(
        text(
            """
            SELECT count(metadata.question_id)::integer AS total_count,
                   count(metadata.question_id)
                       FILTER (WHERE question.status = 'published')::integer
                       AS published_count,
                   count(metadata.question_id)
                       FILTER (WHERE question.status = 'draft')::integer AS draft_count,
                   count(DISTINCT metadata.task_number)::integer AS task_count,
                   count(DISTINCT metadata.period)
                       FILTER (WHERE metadata.period IS NOT NULL)::integer AS period_count,
                   count(metadata.question_id)
                       FILTER (WHERE metadata.review_required)::integer AS review_count
            FROM app.history_question_metadata AS metadata
            JOIN app.questions AS question ON question.id = metadata.question_id
            """
        )
    )
    row = summary.mappings().one()
    media = await session.execute(
        text(
            """
            SELECT count(*)::integer
            FROM app.media_assets
            WHERE object_key LIKE 'history-trainer/%'
            """
        )
    )
    tasks_result = await session.execute(
        text(
            """
            SELECT metadata.task_number, count(*)::integer AS question_count
            FROM app.history_question_metadata AS metadata
            GROUP BY metadata.task_number
            ORDER BY metadata.task_number
            """
        )
    )
    return {
        "imported": row["total_count"] > 0,
        "totalQuestionCount": row["total_count"],
        "publishedQuestionCount": row["published_count"],
        "draftQuestionCount": row["draft_count"],
        "taskCount": row["task_count"],
        "periodCount": row["period_count"],
        "reviewRequiredCount": row["review_count"],
        "imageCount": media.scalar_one(),
        "tasks": [
            {"number": task["task_number"], "questionCount": task["question_count"]}
            for task in tasks_result.mappings()
        ],
    }


@router.get(
    "/question-error-reports",
    operation_id="adminListQuestionErrorReports",
    include_in_schema=False,
)
async def admin_list_question_error_reports(
    admin: AdminPrincipal,
    session: Session,
    report_status: Annotated[
        Literal["all", "open", "answered", "new", "in_progress", "resolved"], Query(alias="status")
    ] = "all",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    del admin
    offset = _offset(cursor)
    conditions = ["true"]
    parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
    if report_status != "all":
        conditions.append("report.status=:report_status" if report_status in ("open","answered") else "report.workflow_status=:report_status")
        parameters["report_status"] = report_status
    result = await session.execute(
        text(
            f"""
            SELECT report.id,report.question_id,report.question_version,
                   report.attempt_question_id,report.message,report.status,
                   report.admin_reply,report.created_at,report.replied_at,report.workflow_status,
                   version.prompt,metadata.task_number,
                   reporter.email AS reporter_email,
                   reporter.display_name AS reporter_name
            FROM app.question_error_reports AS report
            LEFT JOIN app.question_versions AS version
              ON version.question_id=report.question_id
             AND version.version=report.question_version
            LEFT JOIN app.history_question_metadata AS metadata
              ON metadata.question_id=report.question_id
            LEFT JOIN app.users AS reporter ON reporter.id=report.user_id
            WHERE {' AND '.join(conditions)}
            ORDER BY (report.status='open') DESC,report.created_at DESC
            OFFSET :offset LIMIT :fetch_limit
            """
        ),
        parameters,
    )
    rows = list(result.mappings())
    open_result = await session.execute(
        text("SELECT count(*)::integer FROM app.question_error_reports WHERE workflow_status<>'resolved'")
    )
    return {
        "items": [
            {
                "id": row["id"],
                "questionId": row["question_id"],
                "questionVersion": row["question_version"],
                "attemptQuestionId": row["attempt_question_id"],
                "taskNumber": row["task_number"],
                "questionPreview": (row["prompt"] or {}).get("text"),
                "message": row["message"],
                "status": row["status"],
                "workflowStatus": row["workflow_status"],
                "adminReply": row["admin_reply"],
                "reporter": (
                    {
                        "name": row["reporter_name"],
                        "email": row["reporter_email"],
                    }
                    if row["reporter_email"]
                    else None
                ),
                "createdAt": row["created_at"],
                "repliedAt": row["replied_at"],
            }
            for row in rows[:limit]
        ],
        "openCount": open_result.scalar_one(),
        "page": _page(offset, limit, len(rows) > limit),
    }


@router.post(
    "/question-error-reports/{report_id}/reply",
    operation_id="adminReplyQuestionErrorReport",
    include_in_schema=False,
)
async def admin_reply_question_error_report(
    report_id: UUID,
    payload: QuestionErrorReportReply,
    admin: AdminPrincipal,
    session: Session,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    del idempotency_key
    message = payload.message.strip()
    if len(message) < 2:
        raise ProblemException(422, "reply_too_short", "Введите ответ ученику")
    result = await session.execute(
        text(
            """
            UPDATE app.question_error_reports
            SET admin_reply=:message,status='answered',workflow_status='resolved',replied_by=:admin_id,
                replied_at=now(),student_viewed_at=NULL,updated_at=now()
            WHERE id=:report_id
            RETURNING id,status,admin_reply,replied_at
            """
        ),
        {"report_id": report_id, "message": message, "admin_id": admin.user_id},
    )
    report = result.mappings().one_or_none()
    if report is None:
        raise ProblemException(404, "question_error_report_not_found", "Report not found")
    await session.commit()
    return {
        "id": report["id"],
        "status": report["status"],
        "adminReply": report["admin_reply"],
        "repliedAt": report["replied_at"],
    }


class ReportWorkflow(BaseModel):
    status: Literal['new','in_progress','resolved']

@router.patch('/question-error-reports/{report_id}/status', include_in_schema=False)
async def update_report_status(report_id: UUID,payload: ReportWorkflow,admin: AdminPrincipal,session: Session):
    result=await session.execute(text("""
      UPDATE app.question_error_reports SET workflow_status=:status,updated_at=now()
      WHERE id=:id RETURNING id,workflow_status
    """),{'id':report_id,'status':payload.status})
    row=result.mappings().one_or_none()
    if row is None:
        raise ProblemException(404,'report_not_found','Обращение не найдено')
    await session.commit()
    return {'id':row['id'],'workflowStatus':row['workflow_status']}
