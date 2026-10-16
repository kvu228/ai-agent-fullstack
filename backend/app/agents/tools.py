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


def get_tools(vector_store: DocumentVectorStore, k: int = 5) -> list[BaseTool]:
    return [make_search_documents_tool(vector_store, k)]
