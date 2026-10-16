"""Wire nodes and edges into a ReAct-style agent graph.

START -> agent --(has tool calls?)--> tools -> agent -> ... -> END
                \\--(no)----------------------------------> END
"""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents.context import AgentContext
from app.agents.nodes import AgentNode
from app.agents.state import AgentState

AGENT_NODE = "agent"
TOOLS_NODE = "tools"


def build_agent_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[AgentState, AgentContext]:
    # `context_schema` declares what every run must receive via `context=...`.
    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node(AGENT_NODE, AgentNode(model, tools))
    builder.add_node(TOOLS_NODE, ToolNode(list(tools)))

    builder.add_edge(START, AGENT_NODE)
    # `tools_condition` routes to "tools" if the last AI message has tool_calls, else END.
    builder.add_conditional_edges(AGENT_NODE, tools_condition, {"tools": TOOLS_NODE, END: END})
    builder.add_edge(TOOLS_NODE, AGENT_NODE)

    # The checkpointer saves state after every step, keyed by `thread_id`,
    # which gives the agent multi-turn memory (persisted in Postgres).
    return builder.compile(checkpointer=checkpointer)
