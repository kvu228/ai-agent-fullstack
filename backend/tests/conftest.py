"""Shared test fixtures.

Every external dependency is replaced by a fast, local, deterministic stand-in:

    Postgres  -> SQLite file (aiosqlite)          LLM        -> scripted fake chat model
    S3        -> moto (in-process AWS mock)       embeddings -> DeterministicFakeEmbedding
    Chroma    -> in-memory EphemeralClient        checkpoint -> InMemorySaver

So `uv run pytest` needs no Docker, no network and no API key.
"""

import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

import chromadb
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.checkpoint.memory import InMemorySaver
from moto import mock_aws
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.agents import build_agent_graph, get_tools
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import create_session_factory
from app.ingestion.pipeline import IngestionPipeline
from app.main import create_app
from app.storage.s3 import S3Storage
from app.vectorstore.chroma import DocumentVectorStore


class FakeToolCallingModel(GenericFakeChatModel):
    """GenericFakeChatModel + a no-op `bind_tools`, so it fits our agent node.

    Streaming is disabled because the fake's streaming drops `tool_calls`;
    each reply is then emitted as a single "token" event.
    """

    disable_streaming: bool = True

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolCallingModel":
        return self


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # ignore the developer's .env
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        s3_endpoint_url=None,
        s3_bucket="test-documents",
        chunk_size=200,
        chunk_overlap=20,
        max_upload_mb=1,
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        yield


@pytest.fixture
def fake_llm() -> FakeToolCallingModel:
    """Tests script the replies with: fake_llm.messages = iter([AIMessage(...), ...])."""
    return FakeToolCallingModel(messages=iter([]))


@pytest.fixture
async def app(
    settings: Settings, aws: None, fake_llm: FakeToolCallingModel
) -> AsyncIterator[FastAPI]:
    # NullPool: no connection outlives a test (SQLite + async + pooling don't mix well).
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)

    storage = S3Storage(settings)
    await storage.ensure_bucket()

    chroma_client = chromadb.EphemeralClient()
    vector_store = DocumentVectorStore(
        chroma_client,
        DeterministicFakeEmbedding(size=64),
        collection_name=f"test-{uuid.uuid4().hex}",  # isolated per test
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    # Same attributes the real lifespan sets (the lifespan itself never runs in tests).
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.state.chroma_client = chroma_client
    app.state.vector_store = vector_store
    app.state.ingestion = IngestionPipeline(
        session_factory, storage, vector_store, settings.chunk_size, settings.chunk_overlap
    )
    app.state.graph = build_agent_graph(
        fake_llm, get_tools(vector_store, k=3), checkpointer=InMemorySaver()
    )
    yield app
    await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def make_client(app: FastAPI) -> Any:
    """For tests that need several users at once (each client keeps its own cookies)."""

    def _make() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    return _make


async def register_and_login(client: AsyncClient, email: str = "alice@example.com") -> None:
    password = "correct-horse-battery"
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
