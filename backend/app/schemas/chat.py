"""Request/response contracts for chat and conversations.

Schemas are the public contract between backend and frontend; the frontend
mirrors them in `frontend/src/types/api.ts`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=4000)]
    conversation_id: Annotated[
        uuid.UUID | None,
        Field(description="Omit to start a new conversation."),
    ] = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ConversationMessages(BaseModel):
    conversation_id: uuid.UUID
    messages: list[ChatMessage]


# ---- Streaming events (sent as Server-Sent Events) -------------------------------------

StreamEventType = Literal["metadata", "token", "tool_start", "tool_end", "done", "error"]


class StreamEvent(BaseModel):
    """One SSE event. `type` is sent as the SSE `event:` field, the rest as JSON `data:`."""

    type: StreamEventType
    conversation_id: uuid.UUID | None = None
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
