"""Unit tests for pure building blocks (no app, no HTTP)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from langchain_core.documents import Document as LCDocument

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.ingestion.loaders import load_document


def test_password_hashing() -> None:
    hashed = hash_password("s3cret-pass")
    assert hashed.startswith("$argon2id$")
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip(settings: Settings) -> None:
    user_id = uuid.uuid4()
    token, ttl = create_access_token(user_id, settings)
    assert ttl == settings.access_token_ttl_minutes * 60
    assert decode_access_token(token, settings)["sub"] == str(user_id)


def test_expired_access_token_is_rejected(settings: Settings) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    token = jwt.encode(
        {"sub": "x", "type": "access", "exp": past},
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, settings)


def test_token_signed_with_other_key_is_rejected(settings: Settings) -> None:
    token = jwt.encode(
        {"sub": "x", "type": "access", "exp": 9999999999},
        "attacker-key-that-is-long-enough-for-hs256",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, settings)


def test_production_requires_real_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, environment="production", cookie_secure=True)  # type: ignore[call-arg]


def test_text_loader() -> None:
    docs = load_document("Xin chào 👋".encode(), "text/plain")
    assert docs == [LCDocument(page_content="Xin chào 👋")]


def test_unsupported_content_type() -> None:
    with pytest.raises(ValueError):
        load_document(b"MZ", "application/x-msdownload")
