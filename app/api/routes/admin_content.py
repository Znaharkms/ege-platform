from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import AdminPrincipal
from app.api.routes.admin_practice import _check_etag, _etag, _subject_id
from app.api.routes.catalog import Session, _offset, _page
from app.core.problems import ProblemException
from app.repositories.catalog import CatalogRepository, content_summary

router = APIRouter(prefix="/admin", tags=["Admin content"])
SubjectCode = Literal["history", "society"]
ContentType = Literal["date", "term", "plan", "person", "event", "map", "theory", "other"]
PublicationStatus = Literal["draft", "published", "archived"]
RelationType = Literal["related", "explains", "prerequisite", "mentions"]

ADMIN_CONTENT_SELECT = """
    SELECT ci.id, subj.code AS subject, ci.subject_id,
           ci.content_type_code AS content_type, ci.title, ci.slug,
           ci.summary, ci.body, ci.status::text AS status,
           ci.created_by, ci.updated_by, ci.created_at, ci.updated_at,
           ci.published_at, ci.archived_at,
           sec.id AS section_id, sec.parent_id AS section_parent_id,
           sec.code AS section_code, sec.title AS section_title,
           sec.description AS section_description,
           sec.sort_order AS section_sort_order,
           term.definition AS term_definition
    FROM app.content_items AS ci
    JOIN app.subjects AS subj ON subj.id = ci.subject_id
    LEFT JOIN app.sections AS sec ON sec.id = ci.section_id
    LEFT JOIN app.terms AS term ON term.content_id = ci.id
"""


class ContentAssetWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    asset_id: UUID = Field(alias="assetId")
    role: str = Field(min_length=1, max_length=100)
    sort_order: int = Field(default=0, alias="sortOrder", ge=0)


class ContentRelationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_content_id: UUID = Field(alias="targetContentId")
    relation_type: RelationType = Field(alias="relationType")
    weight: int = Field(default=100, ge=0, le=1000)


class ContentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: SubjectCode
    section_id: UUID | None = Field(default=None, alias="sectionId")
    type: ContentType
    title: str = Field(min_length=1, max_length=500)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    summary: str | None = Field(default=None, max_length=5000)
    aliases: list[str] = Field(default_factory=list)
    details: dict[str, Any]
    assets: list[ContentAssetWrite] = Field(default_factory=list)
    relations: list[ContentRelationWrite] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class ContentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    section_id: UUID | None = Field(default=None, alias="sectionId")
    title: str | None = Field(default=None, min_length=1, max_length=500)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
    summary: str | None = Field(default=None, max_length=5000)
    aliases: list[str] | None = None
    details: dict[str, Any] | None = None
    assets: list[ContentAssetWrite] | None = None
    relations: list[ContentRelationWrite] | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


def _validate_details(content_type: str, details: dict[str, Any]) -> None:
    expected_kind = content_type if content_type in {"date", "term", "plan"} else "generic"
    if details.get("kind") != expected_kind:
        raise ProblemException(
            400,
            "invalid_content_details",
            f"Details kind must be {expected_kind!r}",
        )
    if content_type == "date":
        if not str(details.get("dateLabel") or "").strip() or not str(
            details.get("eventText") or ""
        ).strip():
            raise ProblemException(400, "invalid_date", "Date label and event are required")
        if details.get("precision") not in {
            "day",
            "month",
            "year",
            "range",
            "century",
            "approximate",
            "unknown",
        }:
            raise ProblemException(400, "invalid_date_precision", "Unknown date precision")
        for key in ("dateStart", "dateEnd"):
            if details.get(key):
                try:
                    date.fromisoformat(str(details[key]))
                except ValueError as exc:
                    raise ProblemException(400, "invalid_date", f"Invalid {key}") from exc
    elif content_type == "term":
        if not str(details.get("definition") or "").strip():
            raise ProblemException(400, "invalid_term", "Definition is required")
    elif content_type == "plan":
        if not isinstance(details.get("items"), list):
            raise ProblemException(400, "invalid_plan", "Plan items must be an array")
    elif not isinstance(details.get("body"), dict):
        raise ProblemException(400, "invalid_generic_content", "Generic body must be an object")


async def _aliases(session: Session, content_id: UUID) -> list[str]:
    result = await session.execute(
        text(
            "SELECT alias FROM app.content_aliases "
            "WHERE content_id = :content_id ORDER BY alias"
        ),
        {"content_id": content_id},
    )
    return list(result.scalars())


async def _asset_links(session: Session, content_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT asset_id, role, sort_order
            FROM app.content_assets
            WHERE content_id = :content_id
            ORDER BY sort_order, asset_id
            """
        ),
        {"content_id": content_id},
    )
    return [
        {"assetId": row["asset_id"], "role": row["role"], "sortOrder": row["sort_order"]}
        for row in result.mappings()
    ]


async def _relations(session: Session, content_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT target_content_id, relation_type_code, weight
            FROM app.content_relations
            WHERE source_content_id = :content_id
              AND relation_type_code IN ('related', 'explains', 'prerequisite', 'mentions')
            ORDER BY weight DESC, target_content_id
            """
        ),
        {"content_id": content_id},
    )
    return [
        {
            "targetContentId": row["target_content_id"],
            "relationType": row["relation_type_code"],
            "weight": row["weight"],
        }
        for row in result.mappings()
    ]


async def _content_detail(session: Session, content_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(f"{ADMIN_CONTENT_SELECT} WHERE ci.id = :content_id"),
        {"content_id": content_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    repository = CatalogRepository(session)
    item = content_summary(row)
    item.update(
        {
            "aliases": await _aliases(session, content_id),
            "details": await repository._details(row),
            "assets": await repository._assets(content_id),
            "isFavorite": False,
            "status": row["status"],
            "assetLinks": await _asset_links(session, content_id),
            "relations": await _relations(session, content_id),
            "createdAt": row["created_at"],
            "publishedAt": row["published_at"],
            "archivedAt": row["archived_at"],
            "createdBy": row["created_by"],
            "updatedBy": row["updated_by"],
        }
    )
    return item


async def _replace_aliases(session: Session, content_id: UUID, aliases: list[str]) -> None:
    await session.execute(
        text("DELETE FROM app.content_aliases WHERE content_id = :content_id"),
        {"content_id": content_id},
    )
    cleaned = [alias.strip() for alias in aliases if alias.strip()]
    for alias in dict.fromkeys(cleaned):
        await session.execute(
            text("INSERT INTO app.content_aliases (content_id, alias) VALUES (:id, :alias)"),
            {"id": content_id, "alias": alias},
        )


async def _replace_assets(
    session: Session, content_id: UUID, assets: list[ContentAssetWrite]
) -> None:
    await session.execute(
        text("DELETE FROM app.content_assets WHERE content_id = :content_id"),
        {"content_id": content_id},
    )
    for asset in assets:
        await session.execute(
            text(
                """
                INSERT INTO app.content_assets (content_id, asset_id, role, sort_order)
                VALUES (:content_id, :asset_id, :role, :sort_order)
                """
            ),
            {
                "content_id": content_id,
                "asset_id": asset.asset_id,
                "role": asset.role,
                "sort_order": asset.sort_order,
            },
        )


async def _replace_relations(
    session: Session, content_id: UUID, relations: list[ContentRelationWrite]
) -> None:
    await session.execute(
        text(
            """
            DELETE FROM app.content_relations
            WHERE source_content_id = :content_id
              AND relation_type_code IN ('related', 'explains', 'prerequisite', 'mentions')
            """
        ),
        {"content_id": content_id},
    )
    for relation in relations:
        if relation.target_content_id == content_id:
            raise ProblemException(400, "self_relation", "Content cannot relate to itself")
        await session.execute(
            text(
                """
                INSERT INTO app.content_relations (
                    source_content_id, target_content_id, relation_type_code, weight
                ) VALUES (:source, :target, :relation_type, :weight)
                """
            ),
            {
                "source": content_id,
                "target": relation.target_content_id,
                "relation_type": relation.relation_type,
                "weight": relation.weight,
            },
        )


async def _insert_plan_items(
    session: Session,
    content_id: UUID,
    items: list[dict[str, Any]],
    parent_id: UUID | None = None,
) -> None:
    for index, item in enumerate(items):
        item_text = str(item.get("text") or "").strip()
        if not item_text:
            raise ProblemException(400, "invalid_plan_item", "Plan item text is required")
        item_id = uuid4()
        await session.execute(
            text(
                """
                INSERT INTO app.plan_items (
                    id, plan_content_id, parent_id, item_text, sort_order
                ) VALUES (:id, :content_id, :parent_id, :text, :sort_order)
                """
            ),
            {
                "id": item_id,
                "content_id": content_id,
                "parent_id": parent_id,
                "text": item_text,
                "sort_order": int(item.get("position", index)),
            },
        )
        await _insert_plan_items(
            session, content_id, list(item.get("children") or []), item_id
        )


async def _save_typed(
    session: Session, content_id: UUID, content_type: str, details: dict[str, Any]
) -> None:
    _validate_details(content_type, details)
    if content_type == "date":
        await session.execute(
            text(
                """
                INSERT INTO app.history_dates (
                    content_id, date_label, date_start, date_end, precision, event_text
                ) VALUES (
                    :content_id, :label, :start, :end,
                    CAST(:precision AS app.date_precision), :event
                )
                ON CONFLICT (content_id) DO UPDATE
                SET date_label = EXCLUDED.date_label,
                    date_start = EXCLUDED.date_start,
                    date_end = EXCLUDED.date_end,
                    precision = EXCLUDED.precision,
                    event_text = EXCLUDED.event_text
                """
            ),
            {
                "content_id": content_id,
                "label": str(details["dateLabel"]).strip(),
                "start": details.get("dateStart"),
                "end": details.get("dateEnd"),
                "precision": details["precision"],
                "event": str(details["eventText"]).strip(),
            },
        )
    elif content_type == "term":
        await session.execute(
            text(
                """
                INSERT INTO app.terms (content_id, definition)
                VALUES (:content_id, :definition)
                ON CONFLICT (content_id) DO UPDATE SET definition = EXCLUDED.definition
                """
            ),
            {"content_id": content_id, "definition": str(details["definition"]).strip()},
        )
        await session.execute(
            text("DELETE FROM app.term_features WHERE term_content_id = :content_id"),
            {"content_id": content_id},
        )
        for index, feature in enumerate(details.get("features") or []):
            feature_text = str(feature.get("text") or "").strip()
            if not feature_text:
                continue
            await session.execute(
                text(
                    """
                    INSERT INTO app.term_features (
                        term_content_id, feature_type, feature_text, sort_order
                    ) VALUES (:content_id, :type, :text, :position)
                    """
                ),
                {
                    "content_id": content_id,
                    "type": str(feature.get("type") or "feature").strip(),
                    "text": feature_text,
                    "position": int(feature.get("position", index)),
                },
            )
    elif content_type == "plan":
        await session.execute(
            text(
                """
                INSERT INTO app.plans (content_id, introduction, conclusion)
                VALUES (:content_id, :introduction, :conclusion)
                ON CONFLICT (content_id) DO UPDATE
                SET introduction = EXCLUDED.introduction,
                    conclusion = EXCLUDED.conclusion
                """
            ),
            {
                "content_id": content_id,
                "introduction": details.get("introduction"),
                "conclusion": details.get("conclusion"),
            },
        )
        await session.execute(
            text("DELETE FROM app.plan_items WHERE plan_content_id = :content_id"),
            {"content_id": content_id},
        )
        await _insert_plan_items(session, content_id, details.get("items") or [])
    else:
        await session.execute(
            text("UPDATE app.content_items SET body = CAST(:body AS jsonb) WHERE id = :id"),
            {
                "id": content_id,
                "body": json.dumps(details["body"], ensure_ascii=False),
            },
        )


async def _rebuild_search_document(session: Session, content_id: UUID) -> None:
    item = await _content_detail(session, content_id)
    body_text = " ".join(
        [
            item["summary"] or "",
            json.dumps(item["details"], ensure_ascii=False, default=str),
        ]
    )
    await session.execute(
        text(
            """
            INSERT INTO app.content_search_documents (
                content_id, title_text, aliases_text, body_text
            ) VALUES (:id, :title, :aliases, :body)
            ON CONFLICT (content_id) DO UPDATE
            SET title_text = EXCLUDED.title_text,
                aliases_text = EXCLUDED.aliases_text,
                body_text = EXCLUDED.body_text,
                updated_at = now()
            """
        ),
        {
            "id": content_id,
            "title": item["title"],
            "aliases": " ".join(item["aliases"]),
            "body": body_text,
        },
    )


@router.get("/content-sources", operation_id="adminListContentSources", include_in_schema=False)
async def admin_list_content_sources(
    admin: AdminPrincipal, session: Session
) -> dict[str, Any]:
    del admin
    source_labels = {
        "history_dates": ("История — даты", "history", "date"),
        "history_terms": ("История — термины", "history", "term"),
        "society_terms": ("Обществознание — термины", "society", "term"),
        "society_plans": ("Обществознание — планы", "society", "plan"),
    }
    mapping_result = await session.execute(
        text(
            """
            SELECT source_name, count(*)::integer AS record_count,
                   max(last_imported_at) AS last_imported_at
            FROM app.source_mappings
            WHERE entity_type = 'content_item'
            GROUP BY source_name
            """
        )
    )
    mappings = {row["source_name"]: row for row in mapping_result.mappings()}
    latest_result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (source_name)
                   source_name, id, source_filename, status::text AS status,
                   inserted_count, updated_count, skipped_count, failed_count,
                   started_at, completed_at
            FROM app.import_runs
            WHERE dry_run = false
            ORDER BY source_name, started_at DESC
            """
        )
    )
    latest = {row["source_name"]: row for row in latest_result.mappings()}
    count_result = await session.execute(
        text(
            """
            SELECT subject.code AS subject, content.content_type_code AS type,
                   content.status::text AS status, count(*)::integer AS count
            FROM app.content_items AS content
            JOIN app.subjects AS subject ON subject.id = content.subject_id
            GROUP BY subject.code, content.content_type_code, content.status
            """
        )
    )
    sources = []
    for source_name, (label, subject, content_type) in source_labels.items():
        mapping = mappings.get(source_name)
        run = latest.get(source_name)
        sources.append(
            {
                "sourceName": source_name,
                "label": label,
                "subject": subject,
                "type": content_type,
                "recordCount": mapping["record_count"] if mapping else 0,
                "lastImportedAt": mapping["last_imported_at"] if mapping else None,
                "lastRun": (
                    {
                        "id": run["id"],
                        "filename": run["source_filename"],
                        "status": run["status"],
                        "counts": {
                            "inserted": run["inserted_count"],
                            "updated": run["updated_count"],
                            "skipped": run["skipped_count"],
                            "failed": run["failed_count"],
                        },
                        "startedAt": run["started_at"],
                        "completedAt": run["completed_at"],
                    }
                    if run
                    else None
                ),
            }
        )
    return {
        "sources": sources,
        "contentCounts": [
            {
                "subject": row["subject"],
                "type": row["type"],
                "status": row["status"],
                "count": row["count"],
            }
            for row in count_result.mappings()
        ],
    }


@router.get("/content", operation_id="adminListContent", include_in_schema=False)
async def admin_list_content(
    admin: AdminPrincipal,
    session: Session,
    subject: SubjectCode | None = None,
    content_types: Annotated[list[ContentType] | None, Query(alias="type")] = None,
    section_id: Annotated[UUID | None, Query(alias="sectionId")] = None,
    publication_status: Annotated[PublicationStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    del admin
    offset = _offset(cursor)
    conditions = ["true"]
    parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
    if subject:
        conditions.append("subj.code = :subject")
        parameters["subject"] = subject
    if section_id:
        conditions.append("ci.section_id = :section_id")
        parameters["section_id"] = section_id
    if publication_status:
        conditions.append("ci.status = CAST(:status AS app.publication_status)")
        parameters["status"] = publication_status
    if content_types:
        placeholders = []
        for index, value in enumerate(content_types):
            key = f"type_{index}"
            placeholders.append(f":{key}")
            parameters[key] = value
        conditions.append(f"ci.content_type_code IN ({', '.join(placeholders)})")
    if q and q.strip():
        conditions.append(
            "(app.normalize_search_text(ci.title) LIKE :query "
            "OR app.normalize_search_text(COALESCE(ci.summary, '')) LIKE :query)"
        )
        parameters["query"] = f"%{q.strip().lower()}%"
    result = await session.execute(
        text(
            f"""
            {ADMIN_CONTENT_SELECT}
            WHERE {' AND '.join(conditions)}
            ORDER BY ci.updated_at DESC, ci.id
            OFFSET :offset LIMIT :fetch_limit
            """
        ),
        parameters,
    )
    rows = list(result.mappings())
    items = []
    for row in rows[:limit]:
        item = content_summary(row)
        if row["subject"] == "society" and row["content_type"] == "term":
            item["summary"] = row["term_definition"]
        item["status"] = row["status"]
        item["createdAt"] = row["created_at"]
        item["publishedAt"] = row["published_at"]
        item["archivedAt"] = row["archived_at"]
        items.append(item)
    return {"items": items, "page": _page(offset, limit, len(rows) > limit)}


@router.post(
    "/content",
    status_code=status.HTTP_201_CREATED,
    operation_id="adminCreateContent",
    include_in_schema=False,
)
async def admin_create_content(
    payload: ContentCreate,
    admin: AdminPrincipal,
    session: Session,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    del idempotency_key
    _validate_details(payload.type, payload.details)
    subject_id = await _subject_id(session, payload.subject, payload.section_id)
    content_id = uuid4()
    try:
        await session.execute(
            text(
                """
                INSERT INTO app.content_items (
                    id, subject_id, section_id, content_type_code, title, slug,
                    summary, body, created_by, updated_by
                ) VALUES (
                    :id, :subject_id, :section_id, :type, :title, :slug,
                    :summary, '{}'::jsonb, :admin_id, :admin_id
                )
                """
            ),
            {
                "id": content_id,
                "subject_id": subject_id,
                "section_id": payload.section_id,
                "type": payload.type,
                "title": payload.title,
                "slug": payload.slug,
                "summary": payload.summary,
                "admin_id": admin.user_id,
            },
        )
        await _save_typed(session, content_id, payload.type, payload.details)
        await _replace_aliases(session, content_id, payload.aliases)
        await _replace_assets(session, content_id, payload.assets)
        await _replace_relations(session, content_id, payload.relations)
        await _rebuild_search_document(session, content_id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(409, "content_conflict", "Slug or linked object is invalid") from exc
    detail = await _content_detail(session, content_id)
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


@router.get("/content/{content_id}", operation_id="adminGetContent", include_in_schema=False)
async def admin_get_content(
    content_id: UUID, admin: AdminPrincipal, session: Session, response: Response
) -> dict[str, Any]:
    del admin
    detail = await _content_detail(session, content_id)
    if detail is None:
        raise ProblemException(404, "content_not_found", "Content not found")
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


@router.patch(
    "/content/{content_id}", operation_id="adminUpdateContent", include_in_schema=False
)
async def admin_update_content(
    content_id: UUID,
    payload: ContentPatch,
    admin: AdminPrincipal,
    session: Session,
    response: Response,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> dict[str, Any]:
    if not payload.model_fields_set:
        raise ProblemException(400, "empty_update", "No fields supplied")
    current = await _content_detail(session, content_id)
    if current is None:
        raise ProblemException(404, "content_not_found", "Content not found")
    _check_etag(if_match, current["updatedAt"])
    if "section_id" in payload.model_fields_set:
        await _subject_id(session, current["subject"], payload.section_id)
    details = payload.details if payload.details is not None else current["details"]
    _validate_details(current["type"], details)
    try:
        await session.execute(
            text(
                """
                UPDATE app.content_items
                SET section_id = :section_id, title = :title, slug = :slug,
                    summary = :summary, updated_by = :admin_id, updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": content_id,
                "section_id": payload.section_id
                if "section_id" in payload.model_fields_set
                else (current["section"]["id"] if current["section"] else None),
                "title": payload.title or current["title"],
                "slug": payload.slug or current["slug"],
                "summary": payload.summary
                if "summary" in payload.model_fields_set
                else current["summary"],
                "admin_id": admin.user_id,
            },
        )
        await _save_typed(session, content_id, current["type"], details)
        if payload.aliases is not None:
            await _replace_aliases(session, content_id, payload.aliases)
        if payload.assets is not None:
            await _replace_assets(session, content_id, payload.assets)
        if payload.relations is not None:
            await _replace_relations(session, content_id, payload.relations)
        await _rebuild_search_document(session, content_id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ProblemException(409, "content_conflict", "Slug or linked object is invalid") from exc
    detail = await _content_detail(session, content_id)
    response.headers["ETag"] = _etag(detail["updatedAt"])
    return detail


async def _change_content_status(
    content_id: UUID,
    target: Literal["published", "archived"],
    admin: AdminPrincipal,
    session: Session,
    if_match: str,
) -> dict[str, Any]:
    current = await _content_detail(session, content_id)
    if current is None:
        raise ProblemException(404, "content_not_found", "Content not found")
    _check_etag(if_match, current["updatedAt"])
    _validate_details(current["type"], current["details"])
    await session.execute(
        text(
            """
            UPDATE app.content_items
            SET status = CAST(:status AS app.publication_status),
                published_at = CASE WHEN :status = 'published' THEN now() ELSE published_at END,
                archived_at = CASE WHEN :status = 'archived' THEN now() ELSE NULL END,
                updated_by = :admin_id, updated_at = now()
            WHERE id = :id
            """
        ),
        {"status": target, "admin_id": admin.user_id, "id": content_id},
    )
    await session.commit()
    return await _content_detail(session, content_id)


@router.post(
    "/content/{content_id}/publish",
    operation_id="adminPublishContent",
    include_in_schema=False,
)
async def admin_publish_content(
    content_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    del idempotency_key
    return await _change_content_status(content_id, "published", admin, session, if_match)


@router.post(
    "/content/{content_id}/archive",
    operation_id="adminArchiveContent",
    include_in_schema=False,
)
async def admin_archive_content(
    content_id: UUID,
    admin: AdminPrincipal,
    session: Session,
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> dict[str, Any]:
    del idempotency_key
    return await _change_content_status(content_id, "archived", admin, session, if_match)
