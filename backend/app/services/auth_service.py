"""Authentication use-cases: register, login, refresh-token rotation, logout."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models import RefreshToken, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    access_expires_in: int  # seconds
    refresh_token: str
    refresh_expires_in: int  # seconds


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def register(self, email: str, password: str, full_name: str | None) -> User:
        user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
        self._session.add(user)
        try:
            await self._session.commit()
        except IntegrityError:
            # Rely on the UNIQUE constraint, not a "SELECT then INSERT" (that races).
            await self._session.rollback()
            raise ConflictError("An account with this email already exists") from None
        logger.info("User registered", extra={"user_id": str(user.id)})
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self._session.scalar(select(User).where(User.email == email))
        # Always run one hash verification, even for unknown emails (timing-safe).
        hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
        password_ok = verify_password(password, hashed)
        if user is None or not password_ok or not user.is_active:
            # The client gets the same message for every case (don't reveal which emails
            # exist); our logs may say why. Never the email: it's PII, and people sometimes
            # type their password into the email field.
            if user is None:
                reason = "unknown_email"
            elif not password_ok:
                reason = "wrong_password"
            else:
                reason = "inactive_user"
            # INFO, not WARNING: one failed login is normal. Brute force shows up as a
            # *rate* of these, which is a job for a metric + alarm, not for each log line.
            logger.info(
                "Login failed",
                extra={"reason": reason, "user_id": str(user.id) if user else None},
            )
            raise AuthenticationError("Incorrect email or password")
        logger.info("Login succeeded", extra={"user_id": str(user.id)})
        return user

    async def issue_tokens(self, user: User) -> TokenPair:
        access_token, access_ttl = create_access_token(user.id, self._settings)
        refresh_token = generate_refresh_token()
        refresh_ttl = timedelta(days=self._settings.refresh_token_ttl_days)
        self._session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(refresh_token),
                expires_at=datetime.now(UTC) + refresh_ttl,
            )
        )
        await self._session.commit()
        return TokenPair(access_token, access_ttl, refresh_token, int(refresh_ttl.total_seconds()))

    async def refresh(self, refresh_token: str) -> tuple[User, TokenPair]:
        """Rotate: the old refresh token is revoked and a new pair is issued.

        If an already-revoked token is presented, it was probably stolen and used by
        someone else first -> revoke ALL sessions of that user (reuse detection).
        """
        record = await self._session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        )
        if record is None:
            raise AuthenticationError("Invalid refresh token")

        if record.revoked_at is not None:
            # WARNING: a probable stolen token. We recover on our own (all sessions revoked),
            # so it's not an ERROR, but someone should be able to find and review these.
            logger.warning(
                "Refresh token reuse detected, revoking all sessions",
                extra={"user_id": str(record.user_id)},
            )
            await self._revoke_all(record.user_id)
            raise AuthenticationError("Refresh token reuse detected; please log in again")

        if _as_utc(record.expires_at) <= datetime.now(UTC):
            raise AuthenticationError("Refresh token expired")

        user = await self._session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token")

        record.revoked_at = datetime.now(UTC)
        return user, await self.issue_tokens(user)  # issue_tokens commits both changes

    async def logout(self, refresh_token: str) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_refresh_token(refresh_token),
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()

    async def _revoke_all(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.commit()


def _as_utc(value: datetime) -> datetime:
    # SQLite (used in tests) returns naive datetimes; Postgres returns aware ones.
    return value if value.tzinfo else value.replace(tzinfo=UTC)
