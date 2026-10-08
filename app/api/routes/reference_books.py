from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import AdminPrincipal
from app.api.routes.catalog import Session, _offset, _page
from app.core.problems import ProblemException

router = APIRouter(prefix="/admin/reference-books", tags=["Admin reference books"])

SubjectCode = Literal["history", "society"]
FieldType = Literal["text", "long_text", "number", "date", "boolean"]
BookStatus = Literal["active", "archived"]


class ReferenceFieldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    label: str = Field(min_length=1, max_length=200)
    type: FieldType = "text"
    required: bool = False

    @field_validator("label")
    @classmethod
    def label_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value


class ReferenceFieldUpdate(ReferenceFieldCreate):
    id: UUID | None = None


class ReferenceBookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: SubjectCode
    title: str = Field(min_length=1, max_length=300)
    fields: list[ReferenceFieldCreate] = Field(min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class ReferenceBookPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    title: str = Field(min_length=1, max_length=300)
    fields: list[ReferenceFieldUpdate] = Field(min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class ReferenceEntryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


async def _book_row(session: Session, book_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT book.id, book.code, book.title, book.kind,
                   book.content_type_code, book.source_name, book.status,
                   book.created_at, book.updated_at, book.archived_at,
                   subject.code AS subject, subject.title AS subject_title
            FROM app.reference_books AS book
            JOIN app.subjects AS subject ON subject.id = book.subject_id
            WHERE book.id = :book_id
            """
        ),
        {"book_id": book_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _fields(session: Session, book_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, field_key, label, field_type, is_required, sort_order
            FROM app.reference_fields
            WHERE reference_book_id = :book_id
            ORDER BY sort_order, created_at, id
            """
        ),
        {"book_id": book_id},
    )
    return [
        {
            "id": row["id"],
            "key": row["field_key"],
            "label": row["label"],
            "type": row["field_type"],
            "required": row["is_required"],
            "sortOrder": row["sort_order"],
        }
        for row in result.mappings()
    ]


async def _record_count(session: Session, book: dict[str, Any]) -> int:
    if book["kind"] == "builtin":
        result = await session.execute(
            text(
                """
                SELECT count(*)::integer
                FROM app.source_mappings
                WHERE source_name = :source_name
                  AND entity_type = 'content_item'
                """
            ),
            {"source_name": book["source_name"]},
        )
    else:
        result = await session.execute(
            text(
                """
                SELECT count(*)::integer
                FROM app.reference_entries
                WHERE reference_book_id = :book_id AND is_archived = false
                """
            ),
            {"book_id": book["id"]},
        )
    return int(result.scalar_one())


async def _serialize_book(session: Session, book: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": book["id"],
        "code": book["code"],
        "title": book["title"],
        "subject": book["subject"],
        "subjectTitle": book["subject_title"],
        "kind": book["kind"],
        "contentType": book["content_type_code"],
        "sourceName": book["source_name"],
        "status": book["status"],
        "fields": await _fields(session, book["id"]),
        "recordCount": await _record_count(session, book),
        "createdAt": book["created_at"],
        "updatedAt": book["updated_at"],
        "archivedAt": book["archived_at"],
    }


@router.get("", include_in_schema=False)
async def list_reference_books(
    admin: AdminPrincipal,
    session: Session,
    book_status: Annotated[BookStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    del admin
    conditions = "WHERE book.status = :status" if book_status else ""
    parameters = {"status": book_status} if book_status else {}
    result = await session.execute(
        text(
            f"""
            SELECT book.id, book.code, book.title, book.kind,
                   book.content_type_code, book.source_name, book.status,
                   book.created_at, book.updated_at, book.archived_at,
                   subject.code AS subject, subject.title AS subject_title
            FROM app.reference_books AS book
            JOIN app.subjects AS subject ON subject.id = book.subject_id
            {conditions}
            ORDER BY subject.sort_order, book.created_at, book.title
            """
        ),
        parameters,
    )
    items = []
    for row in result.mappings():
        items.append(await _serialize_book(session, dict(row)))
    return {"items": items}


@router.post("", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_reference_book(
    payload: ReferenceBookCreate,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    subject_result = await session.execute(
        text("SELECT id FROM app.subjects WHERE code = :code AND is_active = true"),
        {"code": payload.subject},
    )
    subject_id = subject_result.scalar_one_or_none()
    if subject_id is None:
        raise ProblemException(404, "subject_not_found", "Subject not found")
    book_id = uuid4()
    try:
        await session.execute(
            text(
                """
                INSERT INTO app.reference_books (
                    id, subject_id, code, title, kind, created_by, updated_by
                ) VALUES (
                    :id, :subject_id, :code, :title, 'custom', :admin_id, :admin_id
                )
                """
            ),
            {
                "id": book_id,
                "subject_id": subject_id,
                "code": f"custom_{book_id.hex}",
                "title": payload.title,
                "admin_id": admin.user_id,
            },
        )
        for position, field in enumerate(payload.fields):
            field_id = uuid4()
            await session.execute(
                text(
                    """
                    INSERT INTO app.reference_fields (
                        id, reference_book_id, field_key, label,
                        field_type, is_required, sort_order
                    ) VALUES (
                        :id, :book_id, :key, :label, :type, :required, :position
                    )
                    """
                ),
                {
                    "id": field_id,
                    "book_id": book_id,
                    "key": f"field_{field_id.hex[:12]}",
                    "label": field.label,
                    "type": field.type,
                    "required": field.required,
                    "position": position,
                },
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(409, "reference_book_conflict", "Reference book conflict") from exc
    book = await _book_row(session, book_id)
    return await _serialize_book(session, book)


@router.get("/{book_id}", include_in_schema=False)
async def get_reference_book(
    book_id: UUID, admin: AdminPrincipal, session: Session, response: Response
) -> dict[str, Any]:
    del admin
    book = await _book_row(session, book_id)
    if book is None:
        raise ProblemException(404, "reference_book_not_found", "Reference book not found")
    response.headers["ETag"] = f'W/"{book["updated_at"].isoformat()}"'
    return await _serialize_book(session, book)


@router.patch("/{book_id}", include_in_schema=False)
async def update_reference_book(
    book_id: UUID,
    payload: ReferenceBookPatch,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    book = await _book_row(session, book_id)
    if book is None:
        raise ProblemException(404, "reference_book_not_found", "Reference book not found")
    if book["kind"] != "custom":
        raise ProblemException(409, "builtin_reference_book", "Built-in settings are fixed")
    existing = {str(field["id"]): field for field in await _fields(session, book_id)}
    supplied_ids = {str(field.id) for field in payload.fields if field.id is not None}
    if not supplied_ids.issubset(existing):
        raise ProblemException(400, "invalid_reference_field", "Unknown reference field")
    removed = [field for field_id, field in existing.items() if field_id not in supplied_ids]
    try:
        for field in removed:
            await session.execute(
                text(
                    """
                    UPDATE app.reference_entries
                    SET values = values - :field_key, updated_at = now(), updated_by = :admin_id
                    WHERE reference_book_id = :book_id
                    """
                ),
                {
                    "field_key": field["key"],
                    "admin_id": admin.user_id,
                    "book_id": book_id,
                },
            )
            await session.execute(
                text("DELETE FROM app.reference_fields WHERE id = :id"),
                {"id": field["id"]},
            )
        for position, field in enumerate(payload.fields):
            if field.id is None:
                field_id = uuid4()
                await session.execute(
                    text(
                        """
                        INSERT INTO app.reference_fields (
                            id, reference_book_id, field_key, label,
                            field_type, is_required, sort_order
                        ) VALUES (
                            :id, :book_id, :key, :label, :type, :required, :position
                        )
                        """
                    ),
                    {
                        "id": field_id,
                        "book_id": book_id,
                        "key": f"field_{field_id.hex[:12]}",
                        "label": field.label,
                        "type": field.type,
                        "required": field.required,
                        "position": position,
                    },
                )
            else:
                await session.execute(
                    text(
                        """
                        UPDATE app.reference_fields
                        SET label = :label, field_type = :type,
                            is_required = :required, sort_order = :position,
                            updated_at = now()
                        WHERE id = :id AND reference_book_id = :book_id
                        """
                    ),
                    {
                        "id": field.id,
                        "book_id": book_id,
                        "label": field.label,
                        "type": field.type,
                        "required": field.required,
                        "position": position,
                    },
                )
        await session.execute(
            text(
                """
                UPDATE app.reference_books
                SET title = :title, updated_by = :admin_id, updated_at = now()
                WHERE id = :book_id
                """
            ),
            {"title": payload.title, "admin_id": admin.user_id, "book_id": book_id},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(409, "reference_book_conflict", "Reference book conflict") from exc
    return await _serialize_book(session, await _book_row(session, book_id))


async def _change_book_status(
    book_id: UUID,
    target: BookStatus,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    book = await _book_row(session, book_id)
    if book is None:
        raise ProblemException(404, "reference_book_not_found", "Reference book not found")
    await session.execute(
        text(
            """
            UPDATE app.reference_books
            SET status = :status,
                archived_at = CASE WHEN :status = 'archived' THEN now() ELSE NULL END,
                updated_at = now(), updated_by = :admin_id
            WHERE id = :book_id
            """
        ),
        {"status": target, "admin_id": admin.user_id, "book_id": book_id},
    )
    await session.commit()
    return await _serialize_book(session, await _book_row(session, book_id))


@router.post("/{book_id}/archive", include_in_schema=False)
async def archive_reference_book(
    book_id: UUID, admin: AdminPrincipal, session: Session
) -> dict[str, Any]:
    return await _change_book_status(book_id, "archived", admin, session)


@router.post("/{book_id}/restore", include_in_schema=False)
async def restore_reference_book(
    book_id: UUID, admin: AdminPrincipal, session: Session
) -> dict[str, Any]:
    return await _change_book_status(book_id, "active", admin, session)


def _validate_entry_values(fields: list[dict[str, Any]], values: dict[str, Any]) -> None:
    allowed = {field["key"]: field for field in fields}
    unknown = set(values) - set(allowed)
    if unknown:
        raise ProblemException(400, "unknown_reference_field", "Unknown reference field")
    for key, field in allowed.items():
        value = values.get(key)
        if field["required"] and (value is None or value == ""):
            raise ProblemException(400, "required_reference_field", f'{field["label"]} is required')
        if value is None or value == "":
            continue
        field_type = field["type"]
        if field_type in {"text", "long_text"} and not isinstance(value, str):
            raise ProblemException(400, "invalid_reference_value", f'{field["label"]} must be text')
        if field_type == "number" and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            raise ProblemException(
                400, "invalid_reference_value", f'{field["label"]} must be numeric'
            )
        if field_type == "boolean" and not isinstance(value, bool):
            raise ProblemException(
                400, "invalid_reference_value", f'{field["label"]} must be boolean'
            )
        if field_type == "date":
            try:
                date.fromisoformat(str(value))
            except ValueError as exc:
                raise ProblemException(
                    400, "invalid_reference_value", f'{field["label"]} must be an ISO date'
                ) from exc


async def _custom_book(session: Session, book_id: UUID) -> dict[str, Any]:
    book = await _book_row(session, book_id)
    if book is None:
        raise ProblemException(404, "reference_book_not_found", "Reference book not found")
    if book["kind"] != "custom":
        raise ProblemException(409, "builtin_reference_book", "Use content API for built-in books")
    return book


@router.get("/{book_id}/entries", include_in_schema=False)
async def list_reference_entries(
    book_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    q: Annotated[str | None, Query(max_length=200)] = None,
    archived: bool = False,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    del admin
    await _custom_book(session, book_id)
    offset = _offset(cursor)
    conditions = ["reference_book_id = :book_id", "is_archived = :archived"]
    parameters: dict[str, Any] = {
        "book_id": book_id,
        "archived": archived,
        "offset": offset,
        "fetch_limit": limit + 1,
    }
    if q and q.strip():
        conditions.append("values::text ILIKE :query")
        parameters["query"] = f"%{q.strip()}%"
    result = await session.execute(
        text(
            f"""
            SELECT id, values, is_archived, created_at, updated_at, archived_at
            FROM app.reference_entries
            WHERE {' AND '.join(conditions)}
            ORDER BY updated_at DESC, id
            OFFSET :offset LIMIT :fetch_limit
            """
        ),
        parameters,
    )
    rows = list(result.mappings())
    items = [
        {
            "id": row["id"],
            "values": row["values"],
            "archived": row["is_archived"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "archivedAt": row["archived_at"],
        }
        for row in rows[:limit]
    ]
    return {"items": items, "page": _page(offset, limit, len(rows) > limit)}


@router.post("/{book_id}/entries", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_reference_entry(
    book_id: UUID,
    payload: ReferenceEntryWrite,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    await _custom_book(session, book_id)
    fields = await _fields(session, book_id)
    _validate_entry_values(fields, payload.values)
    entry_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO app.reference_entries (
                id, reference_book_id, values, created_by, updated_by
            ) VALUES (:id, :book_id, CAST(:values AS jsonb), :admin_id, :admin_id)
            """
        ),
        {
            "id": entry_id,
            "book_id": book_id,
            "values": json.dumps(payload.values, ensure_ascii=False),
            "admin_id": admin.user_id,
        },
    )
    await session.commit()
    return {"id": entry_id, "values": payload.values, "archived": False}


@router.get("/{book_id}/entries/{entry_id}", include_in_schema=False)
async def get_reference_entry(
    book_id: UUID,
    entry_id: UUID,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    del admin
    await _custom_book(session, book_id)
    result = await session.execute(
        text(
            """
            SELECT id, values, is_archived, created_at, updated_at, archived_at
            FROM app.reference_entries
            WHERE id = :entry_id AND reference_book_id = :book_id
            """
        ),
        {"entry_id": entry_id, "book_id": book_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise ProblemException(404, "reference_entry_not_found", "Reference entry not found")
    return {
        "id": row["id"],
        "values": row["values"],
        "archived": row["is_archived"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "archivedAt": row["archived_at"],
    }


@router.patch("/{book_id}/entries/{entry_id}", include_in_schema=False)
async def update_reference_entry(
    book_id: UUID,
    entry_id: UUID,
    payload: ReferenceEntryWrite,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    await _custom_book(session, book_id)
    fields = await _fields(session, book_id)
    _validate_entry_values(fields, payload.values)
    result = await session.execute(
        text(
            """
            UPDATE app.reference_entries
            SET values = CAST(:values AS jsonb), updated_at = now(), updated_by = :admin_id
            WHERE id = :entry_id AND reference_book_id = :book_id
            RETURNING id
            """
        ),
        {
            "entry_id": entry_id,
            "book_id": book_id,
            "values": json.dumps(payload.values, ensure_ascii=False),
            "admin_id": admin.user_id,
        },
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise ProblemException(404, "reference_entry_not_found", "Reference entry not found")
    await session.commit()
    return {"id": entry_id, "values": payload.values}


async def _change_entry_archive(
    book_id: UUID,
    entry_id: UUID,
    archived: bool,
    admin: AdminPrincipal,
    session: Session,
) -> dict[str, Any]:
    await _custom_book(session, book_id)
    result = await session.execute(
        text(
            """
            UPDATE app.reference_entries
            SET is_archived = :archived,
                archived_at = CASE WHEN :archived THEN now() ELSE NULL END,
                updated_at = now(), updated_by = :admin_id
            WHERE id = :entry_id AND reference_book_id = :book_id
            RETURNING id
            """
        ),
        {
            "entry_id": entry_id,
            "book_id": book_id,
            "archived": archived,
            "admin_id": admin.user_id,
        },
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise ProblemException(404, "reference_entry_not_found", "Reference entry not found")
    await session.commit()
    return {"id": entry_id, "archived": archived}


@router.post("/{book_id}/entries/{entry_id}/archive", include_in_schema=False)
async def archive_reference_entry(
    book_id: UUID, entry_id: UUID, admin: AdminPrincipal, session: Session
) -> dict[str, Any]:
    return await _change_entry_archive(book_id, entry_id, True, admin, session)


@router.post("/{book_id}/entries/{entry_id}/restore", include_in_schema=False)
async def restore_reference_entry(
    book_id: UUID, entry_id: UUID, admin: AdminPrincipal, session: Session
) -> dict[str, Any]:
    return await _change_entry_archive(book_id, entry_id, False, admin, session)
