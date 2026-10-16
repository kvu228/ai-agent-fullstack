"""Vector store on ChromaDB, wrapped behind a small, app-specific interface.

Multi-tenancy rule: every chunk carries `user_id` metadata and EVERY search
filters on it, so a user can never retrieve someone else's documents.
"""

import uuid

import chromadb
from chromadb.api import ClientAPI
from langchain_chroma import Chroma
from langchain_core.documents import Document as LCDocument
from langchain_core.embeddings import Embeddings

from app.core.config import Settings


def create_chroma_client(settings: Settings) -> ClientAPI:
    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)


class DocumentVectorStore:
    def __init__(self, client: ClientAPI, embeddings: Embeddings, collection_name: str) -> None:
        self._store = Chroma(
            client=client,
            collection_name=collection_name,
            embedding_function=embeddings,
            # Cosine distance is the usual choice for text embeddings.
            collection_metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _chunk_ids(document_id: uuid.UUID, count: int) -> list[str]:
        # Deterministic ids: re-ingesting overwrites instead of duplicating,
        # and deleting needs no extra lookup.
        return [f"{document_id}:{i}" for i in range(count)]

    async def add_chunks(self, document_id: uuid.UUID, chunks: list[LCDocument]) -> None:
        await self._store.aadd_documents(chunks, ids=self._chunk_ids(document_id, len(chunks)))

    async def delete_document(self, document_id: uuid.UUID, chunk_count: int) -> None:
        if chunk_count:
            await self._store.adelete(ids=self._chunk_ids(document_id, chunk_count))

    async def search(
        self, query: str, user_id: uuid.UUID, k: int
    ) -> list[tuple[LCDocument, float]]:
        """Return the k most relevant chunks of *this user's* documents.

        Each result comes with its cosine similarity (1 = same meaning, 0 = unrelated).
        """
        results = await self._store.asimilarity_search_with_score(
            query, k=k, filter={"user_id": str(user_id)}
        )
        # Chroma returns cosine *distance* (lower is closer): convert to similarity.
        return [(doc, 1.0 - distance) for doc, distance in results]
