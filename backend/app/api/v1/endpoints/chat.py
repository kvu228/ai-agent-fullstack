"""Chat & conversation endpoints. Thin by design: validate, call the service, shape output."""

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import ChatConversation, ChatServiceDep, CurrentUser
from app.schemas.chat import ChatRequest, ConversationMessages, ConversationResponse

router = APIRouter(tags=["chat"])


def _no_transform(response: Response) -> None:
    """FastAPI already sends `Cache-Control: no-cache` for SSE. `no-transform` also tells
    proxies not to gzip the stream: compression (Next.js `next start`, CDNs) buffers the
    events, and the user would get the whole answer at once instead of token by token.

    Must be a dependency: code inside the generator body runs after headers are sent.
    """
    response.headers["Cache-Control"] = "no-transform"


@router.post(
    "/chat/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(_no_transform)],
)
async def chat_stream(
    body: ChatRequest,
    user: CurrentUser,
    conversation: ChatConversation,
    service: ChatServiceDep,
) -> AsyncIterator[ServerSentEvent]:
    """Send a message and receive the answer token-by-token as Server-Sent Events."""
    async for event in service.stream(user, conversation, body.message):
        yield ServerSentEvent(
            event=event.type,
            data=event.model_dump(mode="json", exclude_none=True, exclude={"type"}),
        )


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    user: CurrentUser, service: ChatServiceDep
) -> list[ConversationResponse]:
    return [ConversationResponse.model_validate(c) for c in await service.list_conversations(user)]


@router.get("/conversations/{conversation_id}/messages", response_model=ConversationMessages)
async def conversation_messages(
    conversation_id: uuid.UUID, user: CurrentUser, service: ChatServiceDep
) -> ConversationMessages:
    messages = await service.messages(user, conversation_id)
    return ConversationMessages(conversation_id=conversation_id, messages=messages)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, service: ChatServiceDep
) -> None:
    await service.delete_conversation(user, conversation_id)
