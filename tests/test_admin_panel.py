from fastapi.testclient import TestClient

from app.main import app


def test_admin_panel_is_served() -> None:
    response = TestClient(app).get("/admin")

    assert response.status_code == 200
    assert "Панель администратора" in response.text
    assert "Справочники" in response.text
    assert "Двойной клик по карточке" in response.text
    assert "content-picker" in response.text
    assert "content-workspace" in response.text
    assert "source-cards" in response.text
    assert "Создать справочник" in response.text
    assert "Архивировать справочник" in response.text
    assert "reference-view-list" in response.text
    assert "reference-view-cards" in response.text
    assert "content-view-list" not in response.text
    assert "plan-items-builder" in response.text
    assert 'data-view="questions"' not in response.text
    assert "question-difficulty" not in response.text
    assert "test-slug" not in response.text
    assert "question-image-file" in response.text
    assert "question-validation-warning" in response.text
    assert "test-workspace" in response.text
    assert "question-bank-workspace" in response.text
    assert "history-trainer-view" in response.text
    assert 'data-view="history-trainer"' in response.text
    assert 'data-view="error-reports"' in response.text
    assert "Обращения учеников" in response.text
    assert "error-reports-list" in response.text
    assert "trainer-task-filter" in response.text
    assert "trainer-review-filter" in response.text
    assert "trainer-question-list" in response.text
    assert "self_check" in response.text
    assert "/admin/assets/admin.js" in response.text


def test_admin_panel_assets_are_served() -> None:
    client = TestClient(app)

    script = client.get("/admin/assets/admin.js")
    styles = client.get("/admin/assets/admin.css")

    assert script.status_code == 200
    assert 'const API = "/api/v1"' in script.text
    assert 'button.addEventListener("dblclick"' in script.text
    assert "parseHistoricalDate" in script.text
    assert "fillReferenceSectionFilter" in script.text
    assert "planItemsFromBuilder" in script.text
    assert "openTestWorkspace" in script.text
    assert "/admin/media/images" in script.text
    assert 'throw new Error("Нет текста вопроса")' in script.text
    assert 'throw new Error("Нет ответа")' in script.text
    assert "Сохранить всё равно?" in script.text
    assert "/admin/history-trainer/status" in script.text
    assert "/admin/question-error-reports" in script.text
    assert "replyToErrorReport" in script.text
    assert "trainer-task-grid" not in script.text
    assert "trainer-question-thumbnail" in script.text
    assert 'item.image?.url ? "has-image" : "no-image"' in script.text
    assert styles.status_code == 200
    assert "--brand: #696dce" in styles.text
    assert ".reference-groups.list-view" in styles.text
    assert ".trainer-question-card.has-image" in styles.text
    assert ".trainer-question-thumbnail" in styles.text
    assert ".error-report-card" in styles.text
