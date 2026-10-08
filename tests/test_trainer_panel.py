from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.importing.history_trainer import (
    _clean_multiline,
    _dict,
    _prepare_source_image,
    _values,
)
from app.main import app


def test_public_history_trainer_is_served() -> None:
    client = TestClient(app)

    response = client.get("/trainer/history")

    assert response.status_code == 200
    assert "Тренажёр по истории" in response.text
    assert "trainer-form" in response.text
    assert "attempt-view" in response.text
    assert "result-view" in response.text
    assert "По номеру задания" in response.text
    assert "По периоду" in response.text
    assert "Карты" in response.text
    assert "Культура" in response.text
    assert "limit-question-count" in response.text
    assert "Закончить тест" in response.text
    assert "Главное окно" in response.text
    assert "Сообщить об ошибке в вопросе" in response.text
    assert "Мои сообщения" in response.text
    assert "Изменить настройки" not in response.text
    assert "/trainer/assets/trainer.js" in response.text


def test_public_history_trainer_assets_are_served() -> None:
    client = TestClient(app)

    script = client.get("/trainer/assets/trainer.js")
    styles = client.get("/trainer/assets/trainer.css")

    assert script.status_code == 200
    assert "/history-trainer/config" in script.text
    assert "/history-trainer/count" in script.text
    assert "/history-trainer/attempts" in script.text
    assert "X-Anonymous-Session" in script.text
    assert "historyTrainerAnonymousToken" in script.text
    assert "finishAttemptEarly" in script.text
    assert "modelAnswer" in script.text
    assert "formatSubmittedAnswer" in script.text
    assert '<p><strong>Правильный ответ:</strong></p><p>' in script.text
    assert '<p><strong>Правильный ответ:</strong></p><div class="model-answer">' not in script.text
    assert "/history-trainer/error-reports" in script.text
    assert "submitErrorReport" in script.text
    assert "loadMyReports" in script.text
    assert 'answer.acceptedAnswers.join("\\n")' in script.text
    assert 'answer.optionIds.map((item) => options[item] || item).join("\\n")' in script.text
    assert 'answer.itemIds.map((item) => items[item] || item).join("\\n")' in script.text
    assert 'button.disabled = false' in script.text
    assert styles.status_code == 200
    assert "--brand: #696dce" in styles.text
    assert "@media (max-width: 680px)" in styles.text


def test_multiline_history_question_keeps_each_non_empty_line() -> None:
    source = "Первая строка\r\n\r\n  Вторая   строка \nТретья строка"

    assert _clean_multiline(source) == "Первая строка\nВторая строка\nТретья строка"


def test_history_dictionary_repairs_split_unicode_escape() -> None:
    assert _values(r"{1: '1950\u 2060-е годы'}") == ["1950-е годы"]


def test_history_dictionary_repairs_split_hex_escape() -> None:
    assert _values(r"{1: 'С.\xa 0С. Прокофьев'}") == ["С. С. Прокофьев"]


def test_history_dictionary_preserves_unknown_escape_as_text() -> None:
    assert _dict(r"{1: 'текст\ученик'}") == {1: r"текст\ученик"}


def test_truncated_history_image_is_repaired_and_marked_for_review() -> None:
    source = BytesIO()
    Image.new("RGB", (120, 80), "white").save(source, format="JPEG")

    repaired, warning = _prepare_source_image(source.getvalue()[:-10])

    assert repaired is not None
    assert warning == "повреждённая картинка восстановлена; проверьте её"
    with Image.open(BytesIO(repaired)) as image:
        assert image.size == (120, 80)


def test_unreadable_history_image_is_removed_and_marked_for_review() -> None:
    repaired, warning = _prepare_source_image(b"not an image")

    assert repaired is None
    assert warning == "повреждённая картинка не восстановлена"
