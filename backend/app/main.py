"""Application entrypoint.

Run in development:  uv run fastapi dev app/main.py
Run in production:   uv run fastapi run app/main.py
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agents import build_agent_graph, get_chat_model, get_embeddings, get_tools
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.tracing import configure_tracing
from app.db.session import create_engine, create_session_factory
from app.ingestion.pipeline import IngestionPipeline
from app.storage.s3 import S3Storage
from app.vectorstore.chroma import DocumentVectorStore, create_chroma_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook: build expensive, shared objects exactly once."""
    settings = get_settings()
    # Here and not in create_app(): the lifespan doesn't run in tests, so tests never trace.
    configure_tracing(settings)

    # Postgres (SQLAlchemy) for our own tables: users, documents, conversations...
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    # S3 (Floci locally) for the raw files.
    storage = S3Storage(settings)
    await storage.ensure_bucket()

    # Chroma for the chunk embeddings.
    chroma_client = create_chroma_client(settings)
    vector_store = DocumentVectorStore(
        chroma_client, get_embeddings(settings), settings.chroma_collection
    )

    # Postgres (psycopg pool) for LangGraph checkpoints = the agent's conversation memory.
    checkpoint_pool = AsyncConnectionPool(
        settings.checkpointer_conninfo,
        max_size=settings.db_pool_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await checkpoint_pool.open()
    checkpointer = AsyncPostgresSaver(checkpoint_pool)  # type: ignore[arg-type]
    await checkpointer.setup()  # creates/migrates LangGraph's own tables (idempotent)

    app.state.session_factory = session_factory
    app.state.storage = storage
    app.state.chroma_client = chroma_client
    app.state.vector_store = vector_store
    app.state.ingestion = IngestionPipeline(
        session_factory, storage, vector_store, settings.chunk_size, settings.chunk_overlap
    )
    app.state.graph = build_agent_graph(
        model=get_chat_model(settings),
        tools=get_tools(vector_store, k=settings.retrieval_k),
        checkpointer=checkpointer,
    )
    try:
        yield
    finally:
        # Graceful shutdown: return DB connections instead of dropping them.
        await checkpoint_pool.close()
        await engine.dispose()


def create_app() -> FastAPI:
    """App factory: makes it easy to build isolated apps in tests."""
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        # Hide interactive docs in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )

    # TODO(advanced): add a RequestContextMiddleware (pure ASGI, not BaseHTTPMiddleware,
    # which has a history of trouble with streaming responses and BackgroundTasks): reuse an
    # incoming X-Request-ID or make one, store it in a ContextVar + logging.Filter so every
    # log line carries it, wrap `send` to read the status and echo X-Request-ID back, and log
    # "METHOD path -> status, headers after X ms, total Y ms". Things worth observing:
    #   - /chat/stream: headers go out early, total = the whole agent answer (SSE)
    #   - POST /documents: total includes ingestion (BackgroundTasks run inside the ASGI call)
    # Only needed for clients calling the API directly from another origin. The Next.js
    # frontend proxies /api/* (same origin), so browsers never need CORS for it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
