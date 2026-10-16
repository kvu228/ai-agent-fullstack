"""Chat use-cases: conversations (Postgres) + the LangGraph agent.

Endpoints stay thin (HTTP concerns only) and call this service. This makes
the logic reusable (CLI, worker, tests) and easy to mock.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentContext
from app.agents.graph import AGENT_NODE, TOOLS_NODE
from app.core.exceptions import NotFoundError
from app.db.base import utcnow
from app.models import Conversation, User
from app.schemas.chat import ChatMessage, StreamEvent

logger = logging.getLogger(__name__)


def _config(conversation_id: uuid.UUID) -> RunnableConfig:
    # `thread_id` tells the checkpointer which conversation to load/save.
    return {"configurable": {"thread_id": str(conversation_id)}}


class ChatService:
    def __init__(self, session: AsyncSession, graph: CompiledStateGraph[Any, Any]) -> None:
        self._session = session
        self._graph = graph

    # ---- Conversations ------------------------------------------------------------------

    async def list_conversations(self, owner: User) -> list[Conversation]:
        result = await self._session.scalars(
            select(Conversation)
            .where(Conversation.owner_id == owner.id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result)

    async def get_conversation(self, owner: User, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self._session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.owner_id == owner.id
            )
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    async def get_or_create_conversation(
        self, owner: User, conversation_id: uuid.UUID | None, first_message: str
    ) -> Conversation:
        if conversation_id is not None:
            conversation = await self.get_conversation(owner, conversation_id)
            conversation.updated_at = utcnow()  # most recently used first in the sidebar
        else:
            conversation = Conversation(owner_id=owner.id, title=first_message.strip()[:80])
            self._session.add(conversation)
        await self._session.commit()
        return conversation

    async def messages(self, owner: User, conversation_id: uuid.UUID) -> list[ChatMessage]:
        conversation = await self.get_conversation(owner, conversation_id)  # ownership check
        snapshot = await self._graph.aget_state(_config(conversation.id))
        result: list[ChatMessage] = []
        for msg in snapshot.values.get("messages", []):
            if isinstance(msg, HumanMessage):
                result.append(ChatMessage(role="user", content=msg.text))
            elif isinstance(msg, AIMessage) and msg.text:
                result.append(ChatMessage(role="assistant", content=msg.text))
        return result

    async def delete_conversation(self, owner: User, conversation_id: uuid.UUID) -> None:
        conversation = await self.get_conversation(owner, conversation_id)
        # Delete the stored messages too, not just our row (privacy: "delete" means delete).
        if isinstance(self._graph.checkpointer, BaseCheckpointSaver):
            await self._graph.checkpointer.adelete_thread(str(conversation.id))
        await self._session.delete(conversation)
        await self._session.commit()

    # ---- Agent ------------------------------------------------------------------------

    async def stream(
        self, owner: User, conversation: Conversation, message: str
    ) -> AsyncIterator[StreamEvent]:
        """Yield StreamEvents: metadata -> (token | tool_start | tool_end)* -> done.

        `conversation` must already be ownership-checked (see api/deps.py).
        """
        yield StreamEvent(type="metadata", conversation_id=conversation.id)

        try:
            # Two stream modes at once -> items are (mode, payload) tuples:
            #   "messages": (message_chunk, metadata) for every LLM token
            #   "updates":  {node_name: state_update} after every node finishes
            stream: AsyncIterator[Any] = self._graph.astream(
                {"messages": [HumanMessage(message)]},
                _config(conversation.id),
                context=AgentContext(user_id=owner.id),
                stream_mode=["messages", "updates"],
            )
            async for mode, payload in stream:
                if mode == "messages":
                    chunk, metadata = payload
                    # Usually AIMessageChunk; a full AIMessage if the model doesn't stream.
                    if (
                        isinstance(chunk, AIMessage)
                        and metadata.get("langgraph_node") == AGENT_NODE
                        and chunk.text
                    ):
                        yield StreamEvent(type="token", content=chunk.text)

                elif mode == "updates":
                    for event in _events_from_update(payload):
                        yield event
        except Exception:
            # Headers (200 OK) are already sent, so we report errors in-band.
            logger.exception("Agent stream failed")
            yield StreamEvent(type="error", content="The assistant failed to answer.")
            return

        yield StreamEvent(type="done", conversation_id=conversation.id)


def _events_from_update(update: dict[str, Any]) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    for node, node_update in update.items():
        for msg in (node_update or {}).get("messages", []):
            if node == AGENT_NODE and isinstance(msg, AIMessage):
                events += [
                    StreamEvent(type="tool_start", tool_name=tc["name"], tool_input=tc["args"])
                    for tc in msg.tool_calls
                ]
            elif node == TOOLS_NODE and isinstance(msg, ToolMessage):
                events.append(StreamEvent(type="tool_end", tool_name=msg.name, content=msg.text))
    return events
