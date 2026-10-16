"""Graph nodes. A node is any callable: (state) -> partial state update."""

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool

from app.agents.prompts import SYSTEM_PROMPT
from app.agents.state import AgentState


class AgentNode:
    """The "agent" node: ask the LLM what to do next (answer, or call tools).

    It is a class (not a plain function) so the model is *injected* in
    `__init__`, which keeps the node easy to test with a fake model.
    """

    def __init__(self, model: BaseChatModel, tools: Sequence[BaseTool]) -> None:
        # `bind_tools` sends the tools' JSON schemas to the LLM with every request.
        self._model = model.bind_tools(tools)

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        response = await self._model.ainvoke(messages)
        # Return only the *update*; the `add_messages` reducer appends it.
        return {"messages": [response]}
