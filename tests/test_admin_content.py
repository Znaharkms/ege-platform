from uuid import uuid4

import pytest

from app.api.routes.admin_content import ContentCreate, ContentPatch, _validate_details
from app.core.problems import ProblemException


def test_content_create_uses_contract_camel_case() -> None:
    section_id = uuid4()

    payload = ContentCreate.model_validate(
        {
            "subject": "history",
            "sectionId": str(section_id),
            "type": "date",
            "title": "1812 год",
            "slug": "history-date-1812",
            "summary": "Начало Отечественной войны",
            "aliases": ["Отечественная война"],
            "details": {
                "kind": "date",
                "dateLabel": "1812 год",
                "dateStart": "1812-01-01",
                "dateEnd": "1812-12-31",
                "precision": "year",
                "eventText": "Отечественная война 1812 года",
            },
            "assets": [],
            "relations": [],
        }
    )

    assert payload.section_id == section_id
    assert payload.details["dateLabel"] == "1812 год"


def test_content_patch_tracks_explicit_null_section() -> None:
    payload = ContentPatch.model_validate({"sectionId": None})

    assert payload.section_id is None
    assert "section_id" in payload.model_fields_set


@pytest.mark.parametrize(
    ("content_type", "details"),
    [
        ("date", {"kind": "term", "definition": "Ошибка"}),
        ("term", {"kind": "term", "definition": ""}),
        ("plan", {"kind": "plan", "items": "не массив"}),
    ],
)
def test_content_details_are_validated(content_type: str, details: dict) -> None:
    with pytest.raises(ProblemException) as error:
        _validate_details(content_type, details)

    assert error.value.status == 400


def test_valid_plan_details_are_accepted() -> None:
    _validate_details(
        "plan",
        {
            "kind": "plan",
            "introduction": None,
            "conclusion": None,
            "items": [{"text": "Пункт", "position": 0, "children": []}],
        },
    )
