from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import OptionalPrincipal
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.core.problems import ProblemException
from app.db.session import get_session
from app.repositories.catalog import CatalogRepository

from app.services.search_analytics import SelectedSearchRequest, selected_analytics

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]
SubjectCode = Literal["history", "society"]
ContentType = Literal["date", "term", "plan", "person", "event", "map", "theory", "other"]


def _offset(cursor: str | None) -> int:
    try:
        return decode_cursor(cursor)
    except InvalidCursorError as exc:
        raise ProblemException(
            400, "invalid_cursor", "Invalid pagination cursor", str(exc)
        ) from exc


def _page(offset: int, limit: int, has_more: bool) -> dict[str, object]:
    return {
        "nextCursor": encode_cursor(offset + limit) if has_more else None,
        "hasMore": has_more,
    }


@router.get("/subjects", tags=["Subjects"], operation_id="listSubjects", include_in_schema=False)
async def list_subjects(session: Session) -> dict[str, object]:
    return {"items": await CatalogRepository(session).list_subjects()}


@router.get(
    "/subjects/{subject_code}/sections",
    tags=["Subjects"],
    operation_id="listSubjectSections",
    include_in_schema=False,
)
async def list_subject_sections(subject_code: SubjectCode, session: Session) -> dict[str, object]:
    repository = CatalogRepository(session)
    if not await repository.subject_exists(subject_code):
        raise ProblemException(404, "subject_not_found", "Subject not found")
    return {"items": await repository.list_sections(subject_code)}


@router.get("/search/suggestions", tags=["Search"], include_in_schema=False)
async def suggest_content(
    session: Session,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=10)] = 8,
    subject: SubjectCode | None = None,
    content_type: Annotated[ContentType | None, Query(alias="type")] = None,
) -> dict[str, object]:
    query = " ".join(q.split())
    if len(query) < 2:
        return {"items": []}
    return {"items": await CatalogRepository(session).suggest_content(query, limit, subject, content_type)}


@router.get("/plans/suggestions", tags=["Search"], include_in_schema=False)
async def suggest_plans(
    session: Session,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=10)] = 8,
) -> dict[str, object]:
    query = " ".join(q.split())
    if len(query) < 2:
        return {"items": []}
    return {"items": await CatalogRepository(session).suggest_plans(query, limit)}


@router.get("/content", tags=["Content"], operation_id="listContent", include_in_schema=False)
async def list_content(
    session: Session,
    subject: SubjectCode | None = None,
    content_types: Annotated[list[ContentType] | None, Query(alias="type")] = None,
    section_id: Annotated[UUID | None, Query(alias="sectionId")] = None,
    sort: Literal["title", "newest", "oldest"] = "title",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    offset = _offset(cursor)
    items, has_more = await CatalogRepository(session).list_content(
        subject=subject,
        content_types=list(content_types or []),
        section_id=section_id,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "page": _page(offset, limit, has_more)}


@router.get(
    "/content/{content_id}",
    tags=["Content"],
    operation_id="getContent",
    include_in_schema=False,
)
async def get_content(
    content_id: UUID,
    session: Session,
    response: Response,
    principal: OptionalPrincipal,
) -> dict[str, object]:
    user_id = principal.user_id if principal else None
    item = await CatalogRepository(session).get_content(content_id, user_id)
    if item is None:
        raise ProblemException(404, "content_not_found", "Content not found")
    response.headers["ETag"] = f'W/"{item["updatedAt"].timestamp():.6f}"'
    return item


@router.get(
    "/content/{content_id}/related",
    tags=["Content"],
    operation_id="listRelatedContent",
    include_in_schema=False,
)
async def list_related_content(
    content_id: UUID,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    repository = CatalogRepository(session)
    exists = await session.execute(
        text("SELECT 1 FROM app.content_items WHERE id = :id AND status = 'published'"),
        {"id": content_id},
    )
    if exists.scalar_one_or_none() is None:
        raise ProblemException(404, "content_not_found", "Content not found")
    return {"content": await repository.list_related(content_id, limit), "tests": []}


@router.get("/search", tags=["Search"], operation_id="searchContent", include_in_schema=False)
async def search_content(
    request: Request,
    response: Response,
    session: Session,
    principal: OptionalPrincipal,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    subject: SubjectCode | None = None,
    content_types: Annotated[list[ContentType] | None, Query(alias="type")] = None,
    section_id: Annotated[UUID | None, Query(alias="sectionId")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    query = q.strip()
    if not query:
        raise ProblemException(400, "empty_query", "Search query is empty")
    normalized_result = await session.execute(
        text("SELECT app.normalize_search_text(:query)"), {"query": query}
    )
    normalized_query = normalized_result.scalar_one()
    offset = _offset(cursor)
    items, has_more = await CatalogRepository(session).search(
        query=query,
        normalized_query=normalized_query,
        subject=subject,
        content_types=list(content_types or []),
        section_id=section_id,
        offset=offset,
        limit=limit,
    )
    history_id=None
    if principal is not None and offset==0:
        filters = {
            "types": list(content_types or []),
            "sectionId": str(section_id) if section_id else None,
        }
        history_result=await session.execute(
            text(
                """
                INSERT INTO app.search_history (user_id, query, subject_id, filters)
                VALUES (
                    CAST(:user_id AS uuid), :query,
                    (SELECT id FROM app.subjects WHERE code = :subject),
                    CAST(:filters AS jsonb)
                ) RETURNING id
                """
            ),
            {
                "user_id": principal.user_id,
                "query": query,
                "subject": subject,
                "filters": json.dumps(filters),
            },
        )
        history_id=history_result.scalar_one()
        await session.execute(
            text(
                """
                DELETE FROM app.search_history
                WHERE user_id = :user_id
                  AND id NOT IN (
                      SELECT id
                      FROM app.search_history
                      WHERE user_id = :user_id
                      ORDER BY created_at DESC, id DESC
                      LIMIT 200
                  )
                """
            ),
            {"user_id": principal.user_id},
        )
    if offset==0:
        from app.services.search_analytics import track_search
        await track_search(session,request,response,principal,query,subject,content_types[0] if content_types and len(content_types)==1 else None,'search',history_id)
        await session.commit()
    return {
        "query": query,
        "normalizedQuery": normalized_query,
        "items": items,
        "page": _page(offset, limit, has_more),
    }


@router.post('/search/selection',status_code=204,include_in_schema=False)
async def anonymous_selection(request:Request,response:Response,session:Session,principal:OptionalPrincipal,payload: SelectedSearchRequest):
    if payload.analytics:await selected_analytics(session,request,response,principal,payload)
    await session.commit()
