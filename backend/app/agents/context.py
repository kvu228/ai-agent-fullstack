"""Per-run context: data the graph needs that is NOT part of the conversation state."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContext:
    # Who is asking. Passed with `graph.astream(..., context=AgentContext(...))` and read
    # by tools via `runtime.context`. Never let the LLM supply this as a tool argument:
    # a prompt injection could then search another user's documents.
    user_id: uuid.UUID
