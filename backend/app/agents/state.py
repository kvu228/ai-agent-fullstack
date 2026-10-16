"""The shared state that flows through every node of the graph."""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # `add_messages` is a *reducer*: when a node returns {"messages": [new_msg]},
    # LangGraph appends (or updates by id) instead of overwriting the list.
    messages: Annotated[list[AnyMessage], add_messages]
