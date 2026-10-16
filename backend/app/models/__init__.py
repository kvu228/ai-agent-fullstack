"""Import every model here so `Base.metadata` knows all tables (needed by Alembic)."""

from app.models.conversation import Conversation
from app.models.document import Document, DocumentStatus
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = ["Conversation", "Document", "DocumentStatus", "RefreshToken", "User"]
