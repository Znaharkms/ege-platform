from uuid import uuid4

import pytest

from app.api.routes.reference_books import (
    ReferenceBookCreate,
    ReferenceBookPatch,
    _validate_entry_values,
)
from app.core.problems import ProblemException


def test_reference_book_create_accepts_configurable_fields() -> None:
    payload = ReferenceBookCreate.model_validate(
        {
            "subject": "history",
            "title": "Исторические личности",
            "fields": [
                {"label": "Имя", "type": "text", "required": True},
                {"label": "Годы жизни", "type": "text", "required": False},
            ],
        }
    )

    assert payload.subject == "history"
    assert len(payload.fields) == 2
    assert payload.fields[0].required is True


def test_reference_book_patch_keeps_existing_and_new_fields() -> None:
    field_id = uuid4()
    payload = ReferenceBookPatch.model_validate(
        {
            "title": "Исторические личности",
            "fields": [
                {"id": str(field_id), "label": "Имя", "type": "text", "required": True},
                {"label": "Век", "type": "number", "required": False},
            ],
        }
    )

    assert payload.fields[0].id == field_id
    assert payload.fields[1].id is None


def test_reference_entry_values_are_validated_by_field_settings() -> None:
    fields = [
        {"key": "name", "label": "Имя", "type": "text", "required": True},
        {"key": "year", "label": "Год", "type": "number", "required": False},
    ]

    _validate_entry_values(fields, {"name": "Пётр I", "year": 1672})

    with pytest.raises(ProblemException) as error:
        _validate_entry_values(fields, {"name": "", "year": "1672"})

    assert error.value.status == 400


def test_reference_entry_rejects_unknown_fields() -> None:
    fields = [{"key": "name", "label": "Имя", "type": "text", "required": True}]

    with pytest.raises(ProblemException) as error:
        _validate_entry_values(fields, {"name": "Пётр I", "unexpected": "value"})

    assert error.value.code == "unknown_reference_field"
