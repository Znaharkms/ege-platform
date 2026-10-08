from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import text

from app.api.dependencies import CurrentPrincipal
from app.core.config import get_settings
from app.core.email_delivery import send_login_code
from app.core.problems import ProblemException
from app.core.security import (
    create_access_token,
    generate_email_code,
    generate_refresh_token,
    hash_secret,
    secrets_match,
)
from app.db.session import SessionFactory
from app.repositories.users import get_user_profile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])


class EmailCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class EmailCodeVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    display_name: str | None = Field(
        default=None, alias="displayName", min_length=1, max_length=120
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/api/v1/auth",
    )


@router.post(
    "/email/request-code",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="requestEmailCode",
    include_in_schema=False,
)
async def request_email_code(payload: EmailCodeRequest) -> dict[str, object]:
    settings = get_settings()
    if settings.environment.lower() == "production" and not settings.smtp_host:
        raise ProblemException(
            503,
            "email_delivery_unavailable",
            "Email delivery is not configured",
        )
    email = str(payload.email).strip().lower()
    code = generate_email_code()
    async with SessionFactory.begin() as session:
        recent = await session.execute(
            text(
                """
                SELECT count(*)
                FROM app.email_login_codes
                WHERE lower(email) = :email
                  AND created_at > now() - interval '60 seconds'
                """
            ),
            {"email": email},
        )
        if recent.scalar_one() >= 1:
            raise ProblemException(
                429,
                "email_code_rate_limited",
                "Подождите перед повторной отправкой",
                "Новый код можно запросить через 60 секунд после предыдущего.",
            )
        inserted = await session.execute(
            text(
                """
                INSERT INTO app.email_login_codes (email, code_hash, expires_at)
                VALUES (:email, :code_hash, :expires_at) RETURNING id
                """
            ),
            {
                "email": email,
                "code_hash": hash_secret(f"email:{email}:code:{code}"),
                "expires_at": datetime.now(UTC)
                + timedelta(minutes=settings.email_code_ttl_minutes),
            },
        )
        code_id = inserted.scalar_one()
    async def discard_undelivered():
        async with SessionFactory.begin() as cleanup:
            await cleanup.execute(text("DELETE FROM app.email_login_codes WHERE id=:id"),{"id":code_id})
    if settings.smtp_host:
        try:
            await asyncio.to_thread(send_login_code, settings, email, code)
        except smtplib.SMTPAuthenticationError as exc:
            await discard_undelivered()
            raise ProblemException(
                503, "smtp_authentication_failed", "Не удалось отправить код",
                "Почтовый сервер отклонил вход. Администратору нужно проверить "
                "SMTP-логин и пароль приложения.",
            ) from exc
        except Exception as exc:
            await discard_undelivered()
            logger.error("Не удалось отправить код входа")
            raise ProblemException(
                503, "email_delivery_unavailable", "Не удалось отправить код",
                "Почтовый сервер недоступен или отклонил письмо. Обратитесь к администратору."
            ) from exc
    else:
        logger.warning("КОД ВХОДА для %s: %s", email, code)
    async with SessionFactory.begin() as finalize:
        await finalize.execute(text("UPDATE app.email_login_codes SET consumed_at=now() WHERE lower(email)=:email AND id<>:id AND consumed_at IS NULL"),{"email":email,"id":code_id})
    return {
        "message": "Код входа создан",
        "deliveryMode": "email" if settings.smtp_host else "development",
        "expiresIn": settings.email_code_ttl_minutes * 60,
    }


@router.post(
    "/email/verify-code",
    operation_id="verifyEmailCode",
    include_in_schema=False,
)
async def verify_email_code(
    payload: EmailCodeVerifyRequest,
    request: Request,
    response: Response,
) -> dict[str, object]:
    settings = get_settings()
    email = str(payload.email).strip().lower()
    refresh_token = generate_refresh_token()
    async with SessionFactory() as session:
        result = await session.execute(
            text(
                """
                SELECT id, code_hash, attempt_count
                FROM app.email_login_codes
                WHERE lower(email) = :email
                  AND consumed_at IS NULL
                  AND expires_at > now()
                ORDER BY created_at DESC
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"email": email},
        )
        code_row = result.mappings().one_or_none()
        if code_row is None or code_row["attempt_count"] >= 5:
            raise ProblemException(400, "invalid_email_code", "Код неверный или истёк. Введите последний полученный код или запросите новый.")
        code_value = f"email:{email}:code:{payload.code}"
        if not secrets_match(code_value, code_row["code_hash"]):
            await session.execute(
                text(
                    """
                    UPDATE app.email_login_codes
                    SET attempt_count = LEAST(attempt_count + 1, 5)
                    WHERE id = :id
                    """
                ),
                {"id": code_row["id"]},
            )
            await session.commit()
            raise ProblemException(400, "invalid_email_code", "Код неверный или истёк. Введите последний полученный код или запросите новый.")

        user_result = await session.execute(
            text(
                """
                SELECT id, role::text AS role, status::text AS status
                FROM app.users
                WHERE lower(email) = :email
                  AND status <> 'deleted'
                FOR UPDATE
                """
            ),
            {"email": email},
        )
        user = user_result.mappings().one_or_none()
        is_new_user = user is None
        if user is None:
            display_name = (payload.display_name or "").strip()
            if not display_name:
                raise ProblemException(422, "registration_name_required", "Укажите ваше имя или ник для регистрации")
            user_result = await session.execute(
                text(
                    """
                    INSERT INTO app.users (email, display_name, last_login_at)
                    VALUES (:email, :display_name, now())
                    RETURNING id, role::text AS role, status::text AS status
                    """
                ),
                {"email": email, "display_name": display_name},
            )
            user = user_result.mappings().one()
        elif user["status"] != "active":
            raise ProblemException(403, "account_inactive", "Доступ к аккаунту заблокирован. Обратитесь к администратору.")
        else:
            await session.execute(
                text("UPDATE app.users SET last_login_at = now() WHERE id = :id"),
                {"id": user["id"]},
            )

        await session.execute(
            text(
                """
                INSERT INTO app.external_identities (
                    user_id, provider, provider_user_id, verified_at
                )
                VALUES (:user_id, 'email', :email, now())
                ON CONFLICT (provider, provider_user_id) DO UPDATE
                SET verified_at = now()
                """
            ),
            {"user_id": user["id"], "email": email},
        )
        await session.execute(
            text("UPDATE app.email_login_codes SET consumed_at = now() WHERE id = :id"),
            {"id": code_row["id"]},
        )
        session_result = await session.execute(
            text(
                """
                INSERT INTO app.sessions (
                    user_id, refresh_token_hash, client, user_agent, ip_address, expires_at
                )
                VALUES (
                    :user_id, :refresh_hash, 'web', :user_agent, :ip_address, :expires_at
                )
                RETURNING id
                """
            ),
            {
                "user_id": user["id"],
                "refresh_hash": hash_secret(refresh_token),
                "user_agent": request.headers.get("user-agent"),
                "ip_address": _client_ip(request),
                "expires_at": datetime.now(UTC)
                + timedelta(days=settings.refresh_token_ttl_days),
            },
        )
        session_id = session_result.scalar_one()
        profile = await get_user_profile(session, user["id"])
        await session.commit()

    _set_refresh_cookie(response, refresh_token)
    return {
        "accessToken": create_access_token(
            user_id=user["id"], role=user["role"], session_id=session_id
        ),
        "tokenType": "Bearer",
        "expiresIn": settings.access_token_ttl_minutes * 60,
        "user": profile,
        "isNewUser": is_new_user,
    }


@router.post("/refresh", operation_id="refreshAccessToken", include_in_schema=False)
async def refresh_access_token(
    request: Request,
    response: Response,
) -> dict[str, object]:
    settings = get_settings()
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise ProblemException(401, "refresh_token_missing", "Refresh token is missing")
    new_refresh_token = generate_refresh_token()
    async with SessionFactory.begin() as session:
        result = await session.execute(
            text(
                """
                SELECT s.id, s.user_id, u.role::text AS role, u.status::text AS status
                FROM app.sessions AS s
                JOIN app.users AS u ON u.id = s.user_id
                WHERE s.refresh_token_hash = :token_hash
                  AND s.revoked_at IS NULL
                  AND s.expires_at > now()
                FOR UPDATE OF s
                """
            ),
            {"token_hash": hash_secret(refresh_token)},
        )
        session_row = result.mappings().one_or_none()
        if session_row is None or session_row["status"] != "active":
            raise ProblemException(401, "invalid_refresh_token", "Invalid refresh token")
        await session.execute(
            text(
                """
                UPDATE app.sessions
                SET refresh_token_hash = :new_hash, last_used_at = now()
                WHERE id = :session_id
                """
            ),
            {
                "new_hash": hash_secret(new_refresh_token),
                "session_id": session_row["id"],
            },
        )
    _set_refresh_cookie(response, new_refresh_token)
    return {
        "accessToken": create_access_token(
            user_id=session_row["user_id"],
            role=session_row["role"],
            session_id=session_row["id"],
        ),
        "tokenType": "Bearer",
        "expiresIn": settings.access_token_ttl_minutes * 60,
    }


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="logout",
    include_in_schema=False,
)
async def logout(principal: CurrentPrincipal) -> Response:
    async with SessionFactory.begin() as session:
        await session.execute(
            text("UPDATE app.sessions SET revoked_at = now() WHERE id = :session_id"),
            {"session_id": principal.session_id},
        )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response
