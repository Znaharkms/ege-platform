from uuid import uuid4

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_email_code,
    generate_refresh_token,
    hash_secret,
    secrets_match,
)


def test_access_token_round_trip() -> None:
    user_id = uuid4()
    session_id = uuid4()
    encoded = create_access_token(user_id=user_id, role="student", session_id=session_id)
    decoded = decode_access_token(encoded)
    assert decoded.user_id == user_id
    assert decoded.session_id == session_id
    assert decoded.role == "student"


def test_invalid_access_token_is_rejected() -> None:
    with pytest.raises(TokenError):
        decode_access_token("not-a-jwt")


def test_email_code_has_six_digits() -> None:
    code = generate_email_code()

    assert len(code) == 6
    assert code.isdigit()


def test_secret_hash_is_stable_and_not_plaintext() -> None:
    digest = hash_secret("secret-value")

    assert digest != "secret-value"
    assert secrets_match("secret-value", digest)
    assert not secrets_match("other-value", digest)


def test_refresh_token_has_sufficient_entropy() -> None:
    assert len(generate_refresh_token()) >= 64
