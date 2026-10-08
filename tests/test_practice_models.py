from uuid import uuid4

from app.api.routes.practice import (
    HistoryTrainerCountRequest,
    HistoryTrainerStartRequest,
    QuestionErrorReportCreate,
    StartAttemptRequest,
    SubmitAnswerRequest,
    _history_trainer_filters,
)
from app.importing.history_trainer import _categories


def test_start_attempt_uses_contract_camel_case() -> None:
    test_id = uuid4()

    payload = StartAttemptRequest.model_validate({"testId": str(test_id)})

    assert payload.test_id == test_id


def test_submit_answer_uses_contract_camel_case() -> None:
    question_id = uuid4()

    payload = SubmitAnswerRequest.model_validate(
        {"attemptQuestionId": str(question_id), "response": {"optionId": "a"}}
    )

    assert payload.attempt_question_id == question_id


def test_question_error_report_uses_attempt_question_and_message() -> None:
    question_id = uuid4()

    payload = QuestionErrorReportCreate.model_validate(
        {"attemptQuestionId": str(question_id), "message": "Неверная дата"}
    )

    assert payload.attempt_question_id == question_id
    assert payload.message == "Неверная дата"


def test_history_trainer_request_uses_filters_and_camel_case() -> None:
    payload = HistoryTrainerStartRequest.model_validate(
        {
            "taskNumbers": [1, 12, 21],
            "periods": ["XX век"],
            "categories": ["maps"],
            "questionCount": 15,
        }
    )

    assert payload.task_numbers == [1, 12, 21]
    assert payload.periods == ["XX век"]
    assert payload.categories == ["maps"]
    assert payload.question_count == 15


def test_history_trainer_count_request_uses_same_filters() -> None:
    payload = HistoryTrainerCountRequest.model_validate(
        {"taskNumbers": [9, 10], "periods": ["Карты"], "categories": ["maps"]}
    )

    assert payload.task_numbers == [9, 10]
    assert payload.periods == ["Карты"]
    assert payload.categories == ["maps"]


def test_history_trainer_uses_all_remaining_questions_by_default() -> None:
    payload = HistoryTrainerStartRequest.model_validate({"taskNumbers": [9]})

    assert payload.question_count is None


def test_maps_filter_uses_task_numbers_9_to_12() -> None:
    conditions, parameters = _history_trainer_filters([], [], ["maps"])

    assert any("metadata.task_number BETWEEN 9 AND 12" in item for item in conditions)
    assert "categories" not in parameters


def test_import_categories_do_not_depend_on_period() -> None:
    assert "maps" in _categories(9)
    assert "maps" in _categories(12)
    assert "maps" not in _categories(8)
    assert "maps" not in _categories(13)
