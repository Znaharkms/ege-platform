from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _section(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row.get("section_id") is None:
        return None
    return {
        "id": row["section_id"],
        "subject": row["subject"],
        "parentId": row.get("section_parent_id"),
        "code": row["section_code"],
        "title": row["section_title"],
        "description": row.get("section_description"),
        "sortOrder": row["section_sort_order"],
    }


def content_sources(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    values = [body.get("legacy_source"), body.get("legacy_sources")]
    for key in ("feature_metadata",):
        values.extend(
            item.get("source_ref") for item in body.get(key, []) if isinstance(item, dict)
        )
    return list(
        dict.fromkeys(str(value).strip() for value in values if value and str(value).strip())
    )


def content_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "subject": row["subject"],
        "section": _section(row),
        "type": row["content_type"],
        "title": row["title"],
        "slug": row["slug"],
        "summary": row.get("summary"),
        "updatedAt": row["updated_at"],
        "dateLabel": row.get("date_label"),
        "eventText": row.get("event_text"),
        "definition": row.get("definition"),
        "sources": content_sources(row.get("body")),
    }


def build_section_tree(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nodes: dict[UUID, dict[str, Any]] = {}
    children: dict[UUID | None, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        node = {
            "id": row["id"],
            "subject": row["subject"],
            "parentId": row["parent_id"],
            "code": row["code"],
            "title": row["title"],
            "description": row["description"],
            "sortOrder": row["sort_order"],
            "children": [],
        }
        nodes[row["id"]] = node
        children[row["parent_id"]].append(node)
    for parent_id, nested in children.items():
        if parent_id is not None and parent_id in nodes:
            nodes[parent_id]["children"] = nested
    return children[None]


COMMON_SELECT = """
    SELECT ci.id,
           subj.code AS subject,
           ci.content_type_code AS content_type,
           ci.title,
           ci.slug,
           ci.summary,
           ci.body,
           ci.updated_at,
           hd.date_label, hd.event_text, term.definition,
           sec.id AS section_id,
           sec.parent_id AS section_parent_id,
           sec.code AS section_code,
           sec.title AS section_title,
           sec.description AS section_description,
           sec.sort_order AS section_sort_order
    FROM app.content_items AS ci
    JOIN app.subjects AS subj ON subj.id = ci.subject_id
    LEFT JOIN app.sections AS sec ON sec.id = ci.section_id
    LEFT JOIN app.history_dates AS hd ON hd.content_id = ci.id
    LEFT JOIN app.terms AS term ON term.content_id = ci.id
"""


def ruler_search_pattern(query: str) -> str | None:
    """Keep a ruler's name and Roman ordinal together, including Russian cases."""
    match = re.search(r"(?<![а-яё])([а-яё]+)\s+([ivxlcdm]+)(?![a-zа-яё0-9])", query.lower())
    if not match:
        return None
    name, ordinal = match.groups()
    if not re.fullmatch(r"m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", ordinal):
        return None
    name = name.replace("ё", "е")
    irregular = {
        "павел": "пав(?:ел|л[а-я]*)", "павла": "пав(?:ел|л[а-я]*)",
        "павлу": "пав(?:ел|л[а-я]*)", "павле": "пав(?:ел|л[а-я]*)",
        "павлом": "пав(?:ел|л[а-я]*)",
    }
    stem = re.sub(r"(?:ом|ем|ой|ей|а|я|у|ю|е|ы|и)$", "", name)
    name_pattern = irregular.get(name, re.escape(stem) + "[а-я]*")
    return rf"\m{name_pattern}\s+{ordinal}\M"


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_subjects(self) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text(
                """
                SELECT code, title, sort_order
                FROM app.subjects
                WHERE is_active = true
                ORDER BY sort_order, title
                """
            )
        )
        return [
            {"code": row["code"], "title": row["title"], "sortOrder": row["sort_order"]}
            for row in result.mappings()
        ]

    async def subject_exists(self, subject_code: str) -> bool:
        result = await self.session.execute(
            text("SELECT 1 FROM app.subjects WHERE code = :code AND is_active = true"),
            {"code": subject_code},
        )
        return result.scalar_one_or_none() is not None

    async def list_sections(self, subject_code: str) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text(
                """
                SELECT sec.id, subj.code AS subject, sec.parent_id, sec.code,
                       sec.title, sec.description, sec.sort_order
                FROM app.sections AS sec
                JOIN app.subjects AS subj ON subj.id = sec.subject_id
                WHERE subj.code = :subject_code
                  AND subj.is_active = true
                  AND sec.is_active = true
                ORDER BY sec.sort_order, sec.title, sec.id
                """
            ),
            {"subject_code": subject_code},
        )
        return build_section_tree(list(result.mappings()))

    async def list_content(
        self,
        *,
        subject: str | None,
        content_types: list[str],
        section_id: UUID | None,
        sort: str,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        conditions = ["ci.status = 'published'", "subj.is_active = true"]
        parameters: dict[str, Any] = {"offset": offset, "fetch_limit": limit + 1}
        if subject is not None:
            conditions.append("subj.code = :subject")
            parameters["subject"] = subject
        if section_id is not None:
            conditions.append("ci.section_id = :section_id")
            parameters["section_id"] = section_id
        if content_types:
            placeholders = []
            for index, content_type in enumerate(content_types):
                key = f"content_type_{index}"
                placeholders.append(f":{key}")
                parameters[key] = content_type
            conditions.append(f"ci.content_type_code IN ({', '.join(placeholders)})")

        ordering = {
            "title": "app.normalize_search_text(ci.title), ci.id",
            "newest": "ci.updated_at DESC, ci.id DESC",
            "oldest": "ci.updated_at, ci.id",
        }[sort]
        statement = text(
            f"""
            {COMMON_SELECT}
            WHERE {' AND '.join(conditions)}
            ORDER BY {ordering}
            OFFSET :offset
            LIMIT :fetch_limit
            """
        )
        result = await self.session.execute(statement, parameters)
        rows = list(result.mappings())
        return [content_summary(row) for row in rows[:limit]], len(rows) > limit

    async def get_content(
        self, content_id: UUID, user_id: UUID | None = None
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                f"""
                {COMMON_SELECT}
                WHERE ci.id = :content_id
                  AND ci.status = 'published'
                  AND subj.is_active = true
                """
            ),
            {"content_id": content_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        item = content_summary(row)
        item["aliases"] = await self._aliases(content_id)
        item["details"] = await self._details(row)
        item["assets"] = await self._assets(content_id)
        item["isFavorite"] = await self._is_favorite(content_id, user_id)
        return item

    async def _is_favorite(self, content_id: UUID, user_id: UUID | None) -> bool:
        if user_id is None:
            return False
        result = await self.session.execute(
            text(
                """
                SELECT 1
                FROM app.favorites
                WHERE user_id = :user_id AND content_id = :content_id
                """
            ),
            {"user_id": user_id, "content_id": content_id},
        )
        return result.scalar_one_or_none() is not None

    async def list_favorites(
        self, *, user_id: UUID, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        result = await self.session.execute(
            text(
                f"""
                {COMMON_SELECT}
                JOIN app.favorites AS favorite ON favorite.content_id = ci.id
                WHERE favorite.user_id = :user_id
                  AND ci.status = 'published'
                  AND subj.is_active = true
                ORDER BY favorite.created_at DESC, ci.id
                OFFSET :offset
                LIMIT :fetch_limit
                """
            ),
            {"user_id": user_id, "offset": offset, "fetch_limit": limit + 1},
        )
        rows = list(result.mappings())
        return [content_summary(row) for row in rows[:limit]], len(rows) > limit

    async def _aliases(self, content_id: UUID) -> list[str]:
        result = await self.session.execute(
            text(
                """
                SELECT alias
                FROM app.content_aliases
                WHERE content_id = :content_id
                ORDER BY alias
                """
            ),
            {"content_id": content_id},
        )
        return list(result.scalars())

    async def _details(self, row: Mapping[str, Any]) -> dict[str, Any]:
        content_id = row["id"]
        content_type = row["content_type"]
        metadata = row.get("body") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        feature_metadata = {
            item["order"]: item for item in metadata.get("feature_metadata", [])
            if isinstance(item, dict) and "order" in item
        }
        if content_type == "date":
            result = await self.session.execute(
                text(
                    """
                    SELECT date_label, date_start, date_end, precision::text, event_text
                    FROM app.history_dates
                    WHERE content_id = :content_id
                    """
                ),
                {"content_id": content_id},
            )
            detail = result.mappings().one()
            return {
                "kind": "date",
                "dateLabel": detail["date_label"],
                "dateStart": detail["date_start"],
                "dateEnd": detail["date_end"],
                "precision": detail["precision"],
                "eventText": detail["event_text"],
            }
        if content_type == "term":
            definition_result = await self.session.execute(
                text("SELECT definition FROM app.terms WHERE content_id = :content_id"),
                {"content_id": content_id},
            )
            features_result = await self.session.execute(
                text(
                    """
                    SELECT feature_type, feature_text, sort_order
                    FROM app.term_features
                    WHERE term_content_id = :content_id
                    ORDER BY sort_order, id
                    """
                ),
                {"content_id": content_id},
            )
            return {
                "kind": "term",
                "definition": definition_result.scalar_one(),
                "features": [
                    {
                        "type": feature["feature_type"],
                        "text": feature["feature_text"],
                        "position": feature["sort_order"],
                        "source": feature_metadata.get(feature["sort_order"], {}).get("source_ref"),
                    }
                    for feature in features_result.mappings()
                ],
            }
        if content_type == "plan":
            plan_result = await self.session.execute(
                text(
                    "SELECT introduction, conclusion FROM app.plans "
                    "WHERE content_id = :content_id"
                ),
                {"content_id": content_id},
            )
            plan = plan_result.mappings().one()
            items_result = await self.session.execute(
                text(
                    """
                    SELECT id, parent_id, item_text, sort_order
                    FROM app.plan_items
                    WHERE plan_content_id = :content_id
                    ORDER BY sort_order, id
                    """
                ),
                {"content_id": content_id},
            )
            return {
                "kind": "plan",
                "requirementNotes": metadata.get("requirement_notes", []),
                "introduction": plan["introduction"],
                "conclusion": plan["conclusion"],
                "items": self._plan_tree(
                    list(items_result.mappings()), metadata.get("points_metadata", [])
                ),
            }
        return {"kind": "generic", "body": row["body"]}

    @staticmethod
    def _plan_tree(
        rows: Sequence[Mapping[str, Any]], metadata: Sequence[Mapping[str, Any]] = ()
    ) -> list[dict[str, Any]]:
        nodes: dict[UUID, dict[str, Any]] = {}
        children: dict[UUID | None, list[dict[str, Any]]] = defaultdict(list)
        points = {item["number"]: item for item in metadata if "number" in item}
        for row in rows:
            point = points.get(row["sort_order"], {}) if row["parent_id"] is None else {}
            node = {
                "id": row["id"],
                "required": bool(point.get("is_required", False)),
                "source": point.get("source_ref"),
                "text": row["item_text"],
                "position": row["sort_order"],
                "children": [],
            }
            nodes[row["id"]] = node
            children[row["parent_id"]].append(node)
        for parent_id, nested in children.items():
            if parent_id is not None and parent_id in nodes:
                nodes[parent_id]["children"] = nested
        return children[None]

    async def _assets(self, content_id: UUID) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text(
                """
                SELECT ma.id, ma.object_key, ma.original_filename, ma.mime_type,
                       ma.byte_size, ma.width, ma.height, ma.alt_text,
                       ma.checksum_sha256, ma.created_at
                FROM app.content_assets AS ca
                JOIN app.media_assets AS ma ON ma.id = ca.asset_id
                WHERE ca.content_id = :content_id
                ORDER BY ca.sort_order, ma.id
                """
            ),
            {"content_id": content_id},
        )
        return [
            {
                "id": asset["id"],
                "url": f"/media/{asset['object_key']}",
                "filename": asset["original_filename"],
                "mimeType": asset["mime_type"],
                "byteSize": asset["byte_size"],
                "width": asset["width"],
                "height": asset["height"],
                "altText": asset["alt_text"],
                "checksumSha256": asset["checksum_sha256"],
                "createdAt": asset["created_at"],
            }
            for asset in result.mappings()
        ]

    async def list_related(self, content_id: UUID, limit: int) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text(
                f"""
                SELECT related.relation_type_code AS relation, target.*
                FROM app.content_relations AS related
                JOIN (
                    {COMMON_SELECT}
                    WHERE ci.status = 'published' AND subj.is_active = true
                ) AS target ON target.id = related.target_content_id
                WHERE related.source_content_id = :content_id
                  AND related.relation_type_code IN (
                      'related', 'explains', 'prerequisite', 'mentions'
                  )
                ORDER BY related.weight DESC, target.title, target.id
                LIMIT :limit
                """
            ),
            {"content_id": content_id, "limit": limit},
        )
        return [
            {"relation": row["relation"], "item": content_summary(row)}
            for row in result.mappings()
        ]

    async def suggest_plans(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        normalized_result = await self.session.execute(
            text("SELECT app.normalize_search_text(:query)"), {"query": query}
        )
        normalized = normalized_result.scalar_one()
        tokens = normalized.split()
        if not tokens:
            return []

        def escape_like(value: str) -> str:
            return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        parameters: dict[str, Any] = {
            "exact": normalized, "prefix": escape_like(normalized) + "%", "limit": limit,
        }
        conditions = []
        for index, token in enumerate(tokens):
            key = f"token_{index}"
            conditions.append(f"app.normalize_search_text(ci.title) LIKE :{key}")
            parameters[key] = "%" + escape_like(token) + "%"
        result = await self.session.execute(
            text(
                f"""
                {COMMON_SELECT}
                WHERE ci.status = 'published' AND subj.is_active = true
                  AND subj.code = 'society' AND ci.content_type_code = 'plan'
                  AND {' AND '.join(conditions)}
                ORDER BY CASE
                    WHEN app.normalize_search_text(ci.title) = :exact THEN 0
                    WHEN app.normalize_search_text(ci.title) LIKE :prefix THEN 1
                    ELSE 2 END,
                    similarity(app.normalize_search_text(ci.title), :exact) DESC,
                    length(ci.title), app.normalize_search_text(ci.title), ci.id
                LIMIT :limit
                """
            ),
            parameters,
        )
        return [content_summary(row) for row in result.mappings()]

    async def suggest_content(
        self, query: str, limit: int = 8, subject: str | None = None, content_type: str | None = None
    ) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text("SELECT app.normalize_search_text(:query)"), {"query": query}
        )
        normalized = result.scalar_one()
        if not normalized.strip():
            return []
        items, _ = await self.search(
            query=query, normalized_query=normalized, subject=subject,
            content_types=[content_type] if content_type else ["date", "term", "plan"], section_id=None,
            offset=0, limit=limit, allow_fuzzy=False,
        )
        return items

    async def search(
        self,
        *,
        query: str,
        normalized_query: str,
        subject: str | None,
        content_types: list[str],
        section_id: UUID | None,
        offset: int,
        limit: int,
        allow_fuzzy: bool = True,
    ) -> tuple[list[dict[str, Any]], bool]:
        conditions = ["ci.status = 'published'", "subj.is_active = true"]
        escaped_query = (
            normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        parameters: dict[str, Any] = {
            "query": query,
            "normalized_query": normalized_query,
            "contains_query": f"%{escaped_query}%",
            "title_prefix": f"{escaped_query}%",
            "allow_fuzzy": allow_fuzzy,
            "offset": offset,
            "fetch_limit": limit + 1,
        }
        if subject is not None:
            conditions.append("subj.code = :subject")
            parameters["subject"] = subject
        if section_id is not None:
            conditions.append("ci.section_id = :section_id")
            parameters["section_id"] = section_id
        if content_types:
            placeholders = []
            for index, content_type in enumerate(content_types):
                key = f"content_type_{index}"
                placeholders.append(f":{key}")
                parameters[key] = content_type
            conditions.append(f"ci.content_type_code IN ({', '.join(placeholders)})")
        ruler_pattern = ruler_search_pattern(normalized_query)
        if ruler_pattern is not None:
            conditions.append("sd.normalized_text ~ :ruler_pattern")
            parameters["ruler_pattern"] = ruler_pattern
        conditions.append(
            """(
                (ci.content_type_code = 'term' AND (
                    app.normalize_search_text(ci.title) LIKE :contains_query
                ))
                OR (ci.content_type_code <> 'term' AND (
                    sd.normalized_text LIKE :contains_query
                    OR sd.search_vector @@ websearch_to_tsquery('pg_catalog.russian', :query)
                    OR (:allow_fuzzy
                        AND similarity(sd.normalized_text, :normalized_query) >= 0.12)
                ))
            )"""
        )
        result = await self.session.execute(
            text(
                f"""
                SELECT base.*,
                       CASE
                           WHEN app.normalize_search_text(base.title) = :normalized_query
                               THEN 100.0
                           WHEN base.exact_alias THEN 95.0
                           WHEN app.normalize_search_text(base.title) LIKE :title_prefix
                               THEN 90.0
                           WHEN base.content_type = 'date'
                                AND base.normalized_text LIKE :contains_query
                               THEN 85.0
                           WHEN app.normalize_search_text(base.title) LIKE :contains_query
                               THEN 80.0
                           WHEN base.search_vector @@
                                websearch_to_tsquery('pg_catalog.russian', :query)
                               THEN 60.0 + ts_rank_cd(
                                   base.search_vector,
                                   websearch_to_tsquery('pg_catalog.russian', :query)
                               )
                           ELSE 40.0 + similarity(base.normalized_text, :normalized_query)
                       END AS score,
                       CASE
                           WHEN app.normalize_search_text(base.title) = :normalized_query
                               THEN 'title'
                           WHEN base.exact_alias THEN 'alias'
                           WHEN base.content_type = 'date'
                                AND base.normalized_text LIKE :contains_query
                               THEN 'date'
                           WHEN app.normalize_search_text(base.title) LIKE :contains_query
                               THEN 'title'
                           WHEN base.search_vector @@
                                websearch_to_tsquery('pg_catalog.russian', :query)
                               THEN 'body'
                           ELSE 'fuzzy'
                       END AS matched_by
                FROM (
                    SELECT common.*,
                           sd.normalized_text,
                           sd.search_vector,
                           EXISTS (
                               SELECT 1
                               FROM app.content_aliases AS ca
                               WHERE ca.content_id = common.id
                                 AND ca.normalized_alias = :normalized_query
                           ) AS exact_alias
                    FROM (
                        {COMMON_SELECT}
                    ) AS common
                    JOIN app.content_search_documents AS sd ON sd.content_id = common.id
                    JOIN app.content_items AS ci ON ci.id = common.id
                    JOIN app.subjects AS subj ON subj.code = common.subject
                    WHERE {' AND '.join(conditions)}
                ) AS base
                ORDER BY score DESC, app.normalize_search_text(base.title), base.id
                OFFSET :offset
                LIMIT :fetch_limit
                """
            ),
            parameters,
        )
        rows = list(result.mappings())
        items = []
        for row in rows[:limit]:
            item = content_summary(row)
            item.update(
                {
                    "score": float(row["score"]),
                    "matchedBy": row["matched_by"],
                    "highlights": [row["summary"]] if row.get("summary") else [],
                }
            )
            items.append(item)
        return items, len(rows) > limit
