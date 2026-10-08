from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.routes.admin_practice import (
    QUESTION_ANSWER_MISSING,
    QUESTION_TEXT_MISSING,
    QuestionCreate,
    _check_etag,
    _etag,
    _question_fingerprint,
    _question_similarity,
    _slugify_test_title,
    _validate_question_content,
    _validate_question_refs,
)
from app.api.routes.admin_practice import (
    TestCreate as AdminTestCreate,
)
from app.api.routes.admin_practice import (
    TestQuestionRef as AdminTestQuestionRef,
)
from app.core.problems import ProblemException


def test_question_create_uses_contract_camel_case() -> None:
    content_id = uuid4()

    payload = QuestionCreate.model_validate(
        {
            "subject": "history",
            "type": "single_choice",
            "prompt": {"text": "В каком году произошло событие?"},
            "answerSpec": {"correctOptionId": "1812"},
            "defaultPoints": 2,
            "contentIds": [str(content_id)],
        }
    )

    assert payload.answer_spec == {"correctOptionId": "1812"}
    assert payload.default_points == 2
    assert payload.content_ids == [content_id]


def test_question_validation_rejects_missing_text_and_answer() -> None:
    with pytest.raises(ProblemException) as error:
        _validate_question_content("text", {"text": "   "}, {"acceptedAnswers": []})

    assert error.value.status == 400
    assert error.value.code == "incomplete_question"
    assert [item["message"] for item in error.value.errors] == [
        QUESTION_TEXT_MISSING,
        QUESTION_ANSWER_MISSING,
    ]


def test_question_validation_accepts_complete_choice() -> None:
    _validate_question_content(
        "single_choice",
        {
            "text": "В каком году произошло событие?",
            "options": [
                {"id": "first", "text": "1812"},
                {"id": "second", "text": "1814"},
            ],
        },
        {"correctOptionId": "first"},
    )


def test_question_fingerprint_ignores_cosmetic_changes_and_generated_ids() -> None:
    first = _question_fingerprint(
        "single_choice",
        {
            "text": "  В каком   году произошло событие? ",
            "options": [
                {"id": "generated-one", "text": "1812"},
                {"id": "generated-two", "text": "1814"},
            ],
            "image": {
                "id": "asset-one",
                "url": "/media/first.webp",
                "checksumSha256": "a" * 64,
            },
        },
    )
    second = _question_fingerprint(
        "single_choice",
        {
            "text": "в каком году произошло событие?",
            "options": [
                {"id": "another-one", "text": "1812"},
                {"id": "another-two", "text": "1814"},
            ],
            "image": {
                "id": "asset-two",
                "url": "/media/second.webp",
                "checksumSha256": "a" * 64,
            },
        },
    )

    assert first == second


def test_question_fingerprint_distinguishes_different_images() -> None:
    prompt = {"text": "Рассмотрите изображение", "image": {"checksumSha256": "a" * 64}}
    other = {"text": "Рассмотрите изображение", "image": {"checksumSha256": "b" * 64}}

    assert _question_fingerprint("map", prompt) != _question_fingerprint("map", other)


def test_similar_question_text_is_detected() -> None:
    first = {"text": "Укажите две причины начала промышленного переворота в России."}
    second = {"text": "Укажите 2 причины начала промышленного переворота в России"}

    assert _question_similarity("text", first, "text", second) >= 0.9


def test_same_generic_text_with_different_images_is_not_duplicate() -> None:
    first = {"text": "Рассмотрите изображение", "image": {"checksumSha256": "a" * 64}}
    second = {"text": "Рассмотрите изображение", "image": {"checksumSha256": "b" * 64}}

    assert _question_similarity("map", first, "map", second) == 0


def test_test_create_parses_question_refs_and_blueprint() -> None:
    section_id = uuid4()
    question_id = uuid4()

    payload = AdminTestCreate.model_validate(
        {
            "subject": "society",
            "sectionId": str(section_id),
            "title": "Экономика",
            "mode": "fixed",
            "timeLimitSeconds": 600,
            "questions": [
                {
                    "questionId": str(question_id),
                    "position": 1,
                    "pointsOverride": 2,
                    "required": True,
                }
            ],
        }
    )

    assert payload.section_id == section_id
    assert payload.time_limit_seconds == 600
    assert payload.questions[0].question_id == question_id
    assert payload.questions[0].points_override == 2


def test_generation_blueprint_uses_contract_camel_case() -> None:
    payload = AdminTestCreate.model_validate(
        {
            "subject": "history",
            "title": "Случайная тренировка",
            "mode": "generated",
            "blueprint": {
                "questionCount": 10,
                "questionTypes": ["single_choice"],
                "randomizeQuestions": True,
            },
        }
    )

    assert payload.blueprint is not None
    assert payload.blueprint.question_count == 10
    assert payload.blueprint.question_types == ["single_choice"]


def test_test_slug_is_generated_from_russian_title() -> None:
    assert _slugify_test_title("Право: задание № 20") == "pravo-zadanie-20"


def test_etag_detects_concurrent_update() -> None:
    updated_at = datetime(2026, 9, 28, 12, 30, 45, 123456, tzinfo=UTC)

    value = _etag(updated_at)
    _check_etag(value, updated_at)

    with pytest.raises(ProblemException) as error:
        _check_etag('W/"old"', updated_at)

    assert error.value.status == 412
    assert error.value.code == "etag_mismatch"


@pytest.mark.parametrize("duplicate", ["question", "position"])
def test_test_question_refs_must_be_unique(duplicate: str) -> None:
    first_id = uuid4()
    second_id = first_id if duplicate == "question" else uuid4()
    second_position = 1 if duplicate == "position" else 2
    questions = [
        AdminTestQuestionRef(questionId=first_id, position=1),
        AdminTestQuestionRef(questionId=second_id, position=second_position),
    ]

    with pytest.raises(ProblemException) as error:
        _validate_question_refs(questions)

    assert error.value.status == 400
