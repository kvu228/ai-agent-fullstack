"""Ingestion pipeline: S3 bytes -> text -> chunks -> embeddings in Chroma.

upload ──► [S3] ──► load (pypdf / utf-8) ──► split ──► embed + store [Chroma]
                                                          │
                                 documents.status = ready / failed  [Postgres]
"""

import asyncio
import logging
import uuid

from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingestion.loaders import load_document
from app.models import Document, DocumentStatus
from app.storage.s3 import S3Storage
from app.vectorstore.chroma import DocumentVectorStore

logger = logging.getLogger(__name__)


class IngestionPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: S3Storage,
        vector_store: DocumentVectorStore,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._vector_store = vector_store
        # Splits on paragraphs, then sentences, then words: keeps chunks semantically whole.
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True
        )

    def split(self, document: Document, pages: list[LCDocument]) -> list[LCDocument]:
        chunks = self._splitter.split_documents(pages)
        for index, chunk in enumerate(chunks):
            # Metadata used for tenant filtering (user_id) and for citations.
            chunk.metadata.update(
                user_id=str(document.owner_id),
                document_id=str(document.id),
                filename=document.filename,
                chunk_index=index,
            )
        return chunks

    async def run(self, document_id: uuid.UUID) -> None:
        """Ingest one document. Runs in the background, so it opens its own DB session."""
        async with self._session_factory() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            try:
                data = await self._storage.download(document.s3_key)
                # Parsing a big PDF is CPU-bound: keep it off the event loop.
                pages = await asyncio.to_thread(load_document, data, document.content_type)
                chunks = self.split(document, pages)
                if not chunks:
                    raise ValueError("No text could be extracted from this file")
                await self._vector_store.add_chunks(document.id, chunks)

                document.chunk_count = len(chunks)
                document.status = DocumentStatus.READY
            except Exception as exc:
                logger.exception("Ingestion failed for document %s", document_id)
                document.status = DocumentStatus.FAILED
                document.error = str(exc)[:500]
            await session.commit()
