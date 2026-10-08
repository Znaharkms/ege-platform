from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.problems import ProblemException
from app.core.security import Principal, TokenError, decode_access_token
from app.db.session import get_session, SessionFactory

bearer = HTTPBearer(auto_error=False)


async def _resolve_principal(
    credentials: HTTPAuthorizationCredentials,
    session: AsyncSession,
) -> Principal:
    try:
        token_principal = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise ProblemException(401, "invalid_token", "Invalid access token") from exc

    result = await session.execute(
        text(
            """
            SELECT u.id, u.role::text AS role, u.status::text AS status
            FROM app.users AS u
            JOIN app.sessions AS s ON s.user_id = u.id
            WHERE u.id = :user_id
              AND s.id = :session_id
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
            """
        ),
        {"user_id": token_principal.user_id, "session_id": token_principal.session_id},
    )
    row = result.mappings().one_or_none()
    if row is None or row["status"] != "active":
        raise ProblemException(401, "session_inactive", "Session is no longer active")

    return Principal(
        user_id=row["id"],
        role=row["role"],
        session_id=token_principal.session_id,
    )


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    if credentials is None:
        raise ProblemException(401, "authentication_required", "Authentication required")
    return await _resolve_principal(credentials, session)


async def get_optional_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal | None:
    if credentials is None:
        return None
    return await _resolve_principal(credentials, session)


async def require_admin(
    principal: Annotated[Principal, Depends(get_current_principal)],
    request: Request,
):
    if principal.role != "admin":
        raise ProblemException(403, "admin_required", "Administrator access required")
    yield principal
    if request.method not in ('GET','HEAD','OPTIONS') or request.url.path.endswith('/export.xlsx'):
        import json
        from uuid import UUID
        entity_id=None
        for value in request.path_params.values():
            try:
                entity_id=UUID(str(value))
                break
            except ValueError:
                continue
        details={'method':request.method,'path':request.url.path}
        if 'application/json' in request.headers.get('content-type',''):
            try:
                payload=await request.json()
                if isinstance(payload,dict):
                    for key in ('status','blocked','subjects','groups'):
                        if key in payload:
                            details[key]=payload[key]
                    if 'notes' in payload:
                        details['notesUpdated']=True
            except (ValueError,RuntimeError):
                pass
        if request.url.path.endswith('/export.xlsx'):
            details['filters']=dict(request.query_params)
        async with SessionFactory.begin() as audit_session:
            await audit_session.execute(text("""
              INSERT INTO app.audit_log(actor_user_id,action,entity_type,entity_id,new_data)
              VALUES (:actor,:action,'admin_request',:entity,CAST(:details AS jsonb))
            """),{'actor':principal.user_id,'action':request.method+' '+request.url.path,
              'entity':entity_id,'details':json.dumps(details)})


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]
AdminPrincipal = Annotated[Principal, Depends(require_admin)]
