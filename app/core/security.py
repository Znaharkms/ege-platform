from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from pydantic import BaseModel

from app.core.config import get_settings


class TokenError(ValueError):
    pass


class Principal(BaseModel):
    user_id: UUID
    role: str
    session_id: UUID


def generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_secret(value: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.jwt_secret.get_secret_value().encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()


def secrets_match(value: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(value), expected_hash)


def create_access_token(*, user_id: UUID, role: str, session_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role,
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> Principal:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "sid", "role", "type", "exp", "iat"]},
        )
        if payload["type"] != "access":
            raise TokenError("Unexpected token type")
        return Principal(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            role=payload["role"],
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise TokenError("Invalid or expired access token") from exc
