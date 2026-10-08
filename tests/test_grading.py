import pytest

from app.services.grading import grade_response


@pytest.mark.parametrize(
    ("question_type", "response", "spec"),
    [
        ("single_choice", {"optionId": "b"}, {"correctOptionId": "b"}),
        (
            "multiple_choice",
            {"optionIds": ["c", "a"]},
            {"correctOptionIds": ["a", "c"]},
        ),
        ("text", {"text": "  Пётр   I "}, {"acceptedAnswers": ["петр i"]}),
        ("matching", {"pairs": {"a": "1"}}, {"pairs": {"a": "1"}}),
        ("ordering", {"itemIds": ["a", "b"]}, {"correctOrder": ["a", "b"]}),
    ],
)
def test_supported_answers_are_graded(
    question_type: str, response: object, spec: dict[str, object]
) -> None:
    result = grade_response(question_type, response, spec)

    assert result.is_correct
    assert result.ratio == 1


def test_wrong_answer_scores_zero() -> None:
    result = grade_response(
        "single_choice", {"optionId": "a"}, {"correctOptionId": "b"}
    )

    assert not result.is_correct
    assert result.ratio == 0


def test_self_check_returns_model_answer_without_automatic_score() -> None:
    result = grade_response(
        "self_check",
        {"text": "Ответ ученика"},
        {"modelAnswer": ["Первый пункт", "Второй пункт"]},
    )

    assert result.is_correct is None
    assert result.ratio is None
    assert result.correct_answer == {"modelAnswer": ["Первый пункт", "Второй пункт"]}
