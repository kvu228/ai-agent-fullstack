"""Password hashing and token primitives. No database or HTTP code here."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings

# Argon2id with library-recommended parameters (the current OWASP recommendation).
_password_hash = PasswordHash.recommended()

# Hash of a random password: used to spend the same time when the user doesn't exist,
# so response timing doesn't reveal which emails are registered.
DUMMY_PASSWORD_HASH = _password_hash.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


# ---- Access token: short-lived, stateless JWT -----------------------------------------


def create_access_token(user_id: uuid.UUID, settings: Settings) -> tuple[str, int]:
    """Return (jwt, lifetime_in_seconds)."""
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, int(ttl.total_seconds())


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Validate signature + expiry and return the claims. Raises jwt.InvalidTokenError."""
    claims: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        # Always pin the algorithm list: never trust the token's own "alg" header.
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp", "type"]},
    )
    if claims["type"] != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return claims


# ---- Refresh token: long-lived, opaque, stored (hashed) in the DB ----------------------


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    # A fast hash is fine here: the token is 384 random bits, not a guessable password.
    return hashlib.sha256(token.encode()).hexdigest()
