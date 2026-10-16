"""Reusable FastAPI dependencies.

`Annotated[X, Depends(get_x)]` aliases keep endpoint signatures short and let
tests swap implementations via `app.dependency_overrides`.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.ingestion.pipeline import IngestionPipeline
from app.models import Conversation, User
from app.schemas.chat import ChatRequest
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.storage.s3 import S3Storage
from app.vectorstore.chroma import DocumentVectorStore

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

SettingsDep = Annotated[Settings, Depends(get_settings)]

# ---- Shared resources (created once in the lifespan, see app/main.py) --------------------


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; always closed, even if the endpoint raises."""
    async with request.app.state.session_factory() as session:
        yield session


def get_storage(request: Request) -> S3Storage:
    storage: S3Storage = request.app.state.storage
    return storage


def get_vector_store(request: Request) -> DocumentVectorStore:
    vector_store: DocumentVectorStore = request.app.state.vector_store
    return vector_store


def get_ingestion(request: Request) -> IngestionPipeline:
    ingestion: IngestionPipeline = request.app.state.ingestion
    return ingestion


def get_graph(request: Request) -> CompiledStateGraph[Any, Any]:
    graph: CompiledStateGraph[Any, Any] = request.app.state.graph
    return graph


DbSession = Annotated[AsyncSession, Depends(get_db)]
StorageDep = Annotated[S3Storage, Depends(get_storage)]
VectorStoreDep = Annotated[DocumentVectorStore, Depends(get_vector_store)]
IngestionDep = Annotated[IngestionPipeline, Depends(get_ingestion)]
GraphDep = Annotated[CompiledStateGraph[Any, Any], Depends(get_graph)]

# ---- Authentication ----------------------------------------------------------------------

# auto_error=False: a missing header is fine, the token may come from the cookie instead.
# Declaring it also adds the "Authorize" button to Swagger UI (/docs).
bearer_scheme = HTTPBearer(auto_error=False)


def _check_csrf_origin(request: Request, settings: Settings) -> None:
    """CSRF defence for cookie-authenticated, state-changing requests.

    Browsers attach cookies automatically, even to requests triggered by other sites.
    Together with SameSite=Lax cookies, rejecting unknown `Origin`s blocks CSRF.
    (Bearer-token requests are immune: browsers never add that header by themselves.)
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        raise PermissionDeniedError("Cross-site request blocked")


async def get_current_user(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is not None:
        token = credentials.credentials
    elif cookie := request.cookies.get(ACCESS_COOKIE):
        _check_csrf_origin(request, settings)
        token = cookie
    else:
        raise AuthenticationError("Not authenticated")

    try:
        claims = decode_access_token(token, settings)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        # Expired, tampered or malformed: the client should call /auth/refresh.
        raise AuthenticationError("Invalid or expired access token") from None

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

# ---- Services ----------------------------------------------------------------------------


def get_auth_service(db: DbSession, settings: SettingsDep) -> AuthService:
    return AuthService(db, settings)


def get_document_service(
    db: DbSession, storage: StorageDep, vector_store: VectorStoreDep, settings: SettingsDep
) -> DocumentService:
    return DocumentService(db, storage, vector_store, settings)


def get_chat_service(db: DbSession, graph: GraphDep) -> ChatService:
    return ChatService(db, graph)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


async def get_chat_conversation(
    body: ChatRequest, user: CurrentUser, service: ChatServiceDep
) -> Conversation:
    """Resolve (or create) the conversation BEFORE the SSE stream starts.

    Once streaming begins the 200 status is already sent, so a 404 for someone
    else's conversation must happen here, in a dependency.
    """
    return await service.get_or_create_conversation(user, body.conversation_id, body.message)


ChatConversation = Annotated[Conversation, Depends(get_chat_conversation)]
