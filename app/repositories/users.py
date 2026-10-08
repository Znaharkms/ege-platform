from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_user_profile(session: AsyncSession, user_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT u.id, u.role::text AS role, u.status::text AS status,
                   u.display_name, u.email, u.avatar_url, u.timezone, u.created_at,
                   COALESCE(
                       array_agg(ei.provider::text ORDER BY ei.provider::text)
                           FILTER (WHERE ei.provider IS NOT NULL),
                       ARRAY[]::text[]
                   ) AS providers
            FROM app.users AS u
            LEFT JOIN app.external_identities AS ei ON ei.user_id = u.id
            WHERE u.id = :user_id
              AND u.status <> 'deleted'
            GROUP BY u.id
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return {
        "id": row["id"],
        "role": row["role"],
        "status": row["status"],
        "displayName": row["display_name"],
        "email": row["email"],
        "avatarUrl": row["avatar_url"],
        "timezone": row["timezone"],
        "providers": list(row["providers"]),
        "createdAt": row["created_at"],
    }
