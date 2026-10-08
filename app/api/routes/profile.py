from __future__ import annotations

from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query, Response, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.dependencies import CurrentPrincipal, OptionalPrincipal
from app.api.routes.catalog import Session, _offset, _page
from app.core.problems import ProblemException
from app.repositories.catalog import CatalogRepository
from app.repositories.users import get_user_profile
from app.services.search_analytics import SelectedSearchRequest, selected_analytics

router = APIRouter(prefix="/me", tags=["Profile"])


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display_name: str | None = Field(default=None, alias="displayName", max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("", operation_id="getCurrentUser", include_in_schema=False)
async def get_current_user(principal: CurrentPrincipal, session: Session) -> dict[str, object]:
    profile = await get_user_profile(session, principal.user_id)
    if profile is None:
        raise ProblemException(404, "user_not_found", "User not found")
    return profile


@router.patch("", operation_id="updateCurrentUser", include_in_schema=False)
async def update_current_user(
    payload: UpdateProfileRequest,
    principal: CurrentPrincipal,
    session: Session,
) -> dict[str, object]:
    if not payload.model_fields_set:
        raise ProblemException(400, "empty_update", "No profile fields supplied")
    assignments: list[str] = []
    parameters: dict[str, object] = {"user_id": principal.user_id}
    if "display_name" in payload.model_fields_set:
        display_name = payload.display_name.strip() if payload.display_name else None
        assignments.append("display_name = :display_name")
        parameters["display_name"] = display_name or None
    if "timezone" in payload.model_fields_set:
        if payload.timezone is None:
            raise ProblemException(400, "invalid_timezone", "Timezone cannot be null")
        try:
            ZoneInfo(payload.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ProblemException(400, "invalid_timezone", "Unknown timezone") from exc
        assignments.append("timezone = :timezone")
        parameters["timezone"] = payload.timezone
    await session.execute(
        text(f"UPDATE app.users SET {', '.join(assignments)} WHERE id = :user_id"),
        parameters,
    )
    await session.commit()
    profile = await get_user_profile(session, principal.user_id)
    if profile is None:
        raise ProblemException(404, "user_not_found", "User not found")
    return profile


@router.get(
    "/search-history",
    operation_id="getSearchHistory",
    include_in_schema=False,
)
async def get_search_history(
    principal: CurrentPrincipal,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, object]:
    result = await session.execute(text("""
        WITH categorized AS (
          SELECT h.*, s.code AS subject, COALESCE(ci.content_type_code,
            CASE WHEN jsonb_array_length(COALESCE(h.filters->'types','[]'::jsonb))=1 THEN h.filters->'types'->>0 ELSE 'all' END) AS category
          FROM app.search_history h LEFT JOIN app.subjects s ON s.id=h.subject_id
          LEFT JOIN app.content_items ci ON ci.id=h.selected_content_id WHERE h.user_id=:user_id
        ), distinct_queries AS (
          SELECT *, row_number() OVER (PARTITION BY subject,category,lower(btrim(query)) ORDER BY created_at DESC,id DESC) AS duplicate_rank FROM categorized
        ), ranked AS (
          SELECT *,row_number() OVER (PARTITION BY subject,category ORDER BY created_at DESC,id DESC) AS category_rank FROM distinct_queries WHERE duplicate_rank=1
        ) SELECT * FROM ranked WHERE category_rank<=:limit ORDER BY created_at DESC,id DESC
    """), {"user_id":principal.user_id,"limit":min(limit,10)})
    return {"items":[{"id":r["id"],"query":r["query"],"subject":r["subject"],"type":r["category"],"selectedContent":str(r["selected_content_id"]) if r["selected_content_id"] else None,"searchedAt":r["created_at"]} for r in result.mappings()]}


@router.post("/search-history",status_code=204,include_in_schema=False)
async def record_selected_search(payload: SelectedSearchRequest,principal: CurrentPrincipal,session: Session,request:Request,response:Response):
    result=await session.execute(text("""INSERT INTO app.search_history(user_id,query,subject_id,filters,selected_content_id)
        SELECT CAST(:user_id AS uuid),:query,ci.subject_id,jsonb_build_object('types',jsonb_build_array(ci.content_type_code),'analytics',CAST(:analytics AS boolean)),ci.id
        FROM app.content_items ci WHERE ci.id=:content_id AND ci.status='published' RETURNING id"""),
        {"user_id":principal.user_id,"query":payload.query.strip(),"content_id":payload.content_id,"analytics":payload.analytics})
    history_id=result.scalar_one_or_none()
    if history_id is None:raise ProblemException(404,"content_not_found","Материал не найден")
    if payload.analytics:
        await selected_analytics(session,request,response,principal,payload,history_id)
    await session.commit()
    return Response(status_code=204)



@router.delete(
    "/search-history",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clearSearchHistory",
    include_in_schema=False,
)
async def clear_search_history(principal: CurrentPrincipal, session: Session) -> Response:
    await session.execute(
        text("DELETE FROM app.search_history WHERE user_id = :user_id"),
        {"user_id": principal.user_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/favorites", operation_id="listFavorites", include_in_schema=False)
async def list_favorites(
    principal: CurrentPrincipal,
    session: Session,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    offset = _offset(cursor)
    items, has_more = await CatalogRepository(session).list_favorites(
        user_id=principal.user_id,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "page": _page(offset, limit, has_more)}


@router.put(
    "/favorites/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="addFavorite",
    include_in_schema=False,
)
async def add_favorite(
    content_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
) -> Response:
    content = await session.execute(
        text("SELECT 1 FROM app.content_items WHERE id = :id AND status = 'published'"),
        {"id": content_id},
    )
    if content.scalar_one_or_none() is None:
        raise ProblemException(404, "content_not_found", "Content not found")
    await session.execute(
        text(
            """
            INSERT INTO app.favorites (user_id, content_id)
            VALUES (:user_id, :content_id)
            ON CONFLICT (user_id, content_id) DO NOTHING
            """
        ),
        {"user_id": principal.user_id, "content_id": content_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/favorites/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeFavorite",
    include_in_schema=False,
)
async def remove_favorite(
    content_id: UUID,
    principal: CurrentPrincipal,
    session: Session,
) -> Response:
    await session.execute(
        text(
            """
            DELETE FROM app.favorites
            WHERE user_id = :user_id AND content_id = :content_id
            """
        ),
        {"user_id": principal.user_id, "content_id": content_id},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

