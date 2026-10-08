from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Grade:
    is_correct: bool | None
    ratio: float | None
    correct_answer: Any


def _normalized_text(value: Any, case_sensitive: bool) -> str:
    text = " ".join(str(value or "").strip().split())
    return text if case_sensitive else text.casefold().replace("ё", "е")


def grade_response(question_type: str, response: Any, answer_spec: dict[str, Any]) -> Grade:
    if question_type == "self_check":
        return Grade(None, None, {"modelAnswer": answer_spec.get("modelAnswer", [])})
    if question_type == "single_choice":
        expected = answer_spec.get("correctOptionId")
        actual = response.get("optionId") if isinstance(response, dict) else response
        return Grade(actual == expected, float(actual == expected), {"optionId": expected})

    if question_type == "multiple_choice":
        expected = set(answer_spec.get("correctOptionIds", []))
        values = response.get("optionIds", []) if isinstance(response, dict) else response
        actual = set(values) if isinstance(values, list) else set()
        correct = bool(expected) and actual == expected
        return Grade(correct, float(correct), {"optionIds": sorted(expected)})

    if question_type == "text":
        case_sensitive = bool(answer_spec.get("caseSensitive", False))
        accepted = {
            _normalized_text(value, case_sensitive)
            for value in answer_spec.get("acceptedAnswers", [])
        }
        value = response.get("text") if isinstance(response, dict) else response
        actual = _normalized_text(value, case_sensitive)
        correct = bool(accepted) and actual in accepted
        return Grade(
            correct,
            float(correct),
            {"acceptedAnswers": answer_spec.get("acceptedAnswers", [])},
        )

    if question_type == "matching":
        expected = answer_spec.get("pairs", {})
        actual = response.get("pairs", {}) if isinstance(response, dict) else {}
        correct = actual == expected
        return Grade(correct, float(correct), {"pairs": expected})

    if question_type == "ordering":
        expected = answer_spec.get("correctOrder", [])
        actual = response.get("itemIds", []) if isinstance(response, dict) else []
        correct = actual == expected and bool(expected)
        return Grade(correct, float(correct), {"itemIds": expected})

    expected = answer_spec.get("correctResponse")
    correct = response == expected and expected is not None
    return Grade(correct, float(correct), expected)
