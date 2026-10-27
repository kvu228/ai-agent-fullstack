"""Chat with the document agent in the terminal, without FastAPI.

    cd backend
    uv run python -m scripts.run_agent --email you@example.com

Searches the documents that user uploaded (needs `docker compose up -d`).
Useful to experiment with prompts, tools and the graph in isolation.
"""

import argparse
import asyncio
import uuid

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.agents import AgentContext, build_agent_graph, get_chat_model, get_embeddings, get_tools
from app.core.config import get_settings
from app.core.tracing import configure_tracing
from app.db.session import create_engine, create_session_factory
from app.models import User
from app.vectorstore.chroma import DocumentVectorStore, create_chroma_client


async def find_user_id(email: str) -> uuid.UUID:
    engine = create_engine(get_settings())
    async with create_session_factory(engine)() as session:
        user_id = await session.scalar(select(User.id).where(User.email == email.lower()))
    await engine.dispose()
    if user_id is None:
        raise SystemExit(f"No user with email {email}")
    return user_id


async def main(email: str) -> None:
    settings = get_settings()
    configure_tracing(settings)
    context = AgentContext(user_id=await find_user_id(email))
    vector_store = DocumentVectorStore(
        create_chroma_client(settings), get_embeddings(settings), settings.chroma_collection
    )
    graph = build_agent_graph(
        get_chat_model(settings),
        get_tools(vector_store, k=settings.retrieval_k),
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}
    print("Type a message (Ctrl+C to quit).")

    while True:
        try:
            # input() blocks, so run it in a thread to keep the event loop free.
            text = (await asyncio.to_thread(input, "\nYou: ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue

        # stream_mode="updates": print each node's output as soon as it finishes.
        async for update in graph.astream(
            {"messages": [HumanMessage(text)]}, config, context=context, stream_mode="updates"
        ):
            for node, state_update in update.items():
                for msg in state_update["messages"]:
                    if msg.type == "ai" and msg.tool_calls:
                        for call in msg.tool_calls:
                            print(f"  [{node}] call {call['name']}({call['args']})")
                    elif msg.type == "tool":
                        print(f"  [{node}] {msg.name} -> {msg.text[:300]}...")
                    else:
                        print(f"Assistant: {msg.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="whose documents to search")
    asyncio.run(main(parser.parse_args().email))
