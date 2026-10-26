"""Tools the agent may call.

The `@tool` decorator turns a typed, documented function into a tool:
- the function name becomes the tool name,
- the docstring becomes the description the LLM reads,
- the type hints become the JSON schema of the arguments.

A parameter typed `ToolRuntime` is injected by LangGraph and hidden from the LLM.
"""

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from app.agents.context import AgentContext
from app.vectorstore.chroma import DocumentVectorStore


def make_search_documents_tool(vector_store: DocumentVectorStore, k: int) -> BaseTool:
    """Factory: the vector store is injected (closure), so tests can pass a fake one."""

    @tool
    async def search_documents(query: str, runtime: ToolRuntime[AgentContext]) -> str:
        """Search the user's uploaded documents and return the most relevant passages.

        Use this for any question about the user's documents. Write `query` as a short,
        specific search phrase (not the whole question). Call it several times with
        different queries if the first results are not enough.
        """
        results = await vector_store.search(query, user_id=runtime.context.user_id, k=k)
        if not results:
            return "No relevant passages found in the user's documents."

        passages = []
        for doc, score in results:
            source = doc.metadata.get("filename", "unknown")
            if page := doc.metadata.get("page"):
                source += f", p.{page}"
            passages.append(f"[{source}] (similarity {score:.2f})\n{doc.page_content}")
        return "\n\n---\n\n".join(passages)

    return search_documents


# TODO(advanced): when there are several tools, turn this module into a package
# (app/agents/tools/{__init__,search,documents}.py), one factory per tool, with
# `get_tools()` in __init__ as the single list passed to both `bind_tools` and `ToolNode`.
# Each new tool keeps the rules of search_documents: deps injected via closure,
# `user_id` from `runtime.context` only, its own DB session from `session_factory`,
# plus a test proving user A cannot reach user B's data through it.
def get_tools(vector_store: DocumentVectorStore, k: int = 5) -> list[BaseTool]:
    return [make_search_documents_tool(vector_store, k)]
