"""Document use-cases: upload, list, download, delete.

Ownership is enforced here: every query filters on `owner_id`, and another
user's document answers 404 (not 403) so ids can't be probed.
"""

import logging
import uuid
from pathlib import PurePath

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotFoundError, PayloadTooLargeError, ValidationFailedError
from app.ingestion.loaders import EXTENSION_TO_CONTENT_TYPE
from app.models import Document, DocumentStatus, User
from app.storage.s3 import S3Storage
from app.vectorstore.chroma import DocumentVectorStore

logger = logging.getLogger(__name__)

PRESIGNED_URL_TTL_SECONDS = 300


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        storage: S3Storage,
        vector_store: DocumentVectorStore,
        settings: Settings,
    ) -> None:
        self._session = session
        self._storage = storage
        self._vector_store = vector_store
        self._max_bytes = settings.max_upload_mb * 1024 * 1024

    async def upload(self, owner: User, file: UploadFile) -> Document:
        # Strip any directory part ("../../etc/passwd" -> "passwd").
        filename = PurePath(file.filename or "untitled").name[:255]
        # Decide the type from the extension allow-list, never trust the client's header.
        content_type = EXTENSION_TO_CONTENT_TYPE.get(PurePath(filename).suffix.lower())
        if content_type is None:
            allowed = ", ".join(EXTENSION_TO_CONTENT_TYPE)
            raise ValidationFailedError(f"Unsupported file type. Allowed: {allowed}")

        # Read at most max+1 bytes so a huge upload can't exhaust memory.
        data = await file.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            raise PayloadTooLargeError(f"File exceeds {self._max_bytes // (1024 * 1024)} MB")
        if not data:
            raise ValidationFailedError("File is empty")

        document_id = uuid.uuid4()
        document = Document(
            id=document_id,
            owner_id=owner.id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            # Random id in the key: no collisions, user-supplied names can't clash.
            s3_key=f"users/{owner.id}/documents/{document_id}/{filename}",
            status=DocumentStatus.PROCESSING,
        )
        # Upload first: if S3 fails we have no orphan DB row pointing to nothing.
        await self._storage.upload(document.s3_key, data, content_type)
        self._session.add(document)
        await self._session.commit()
        return document

    async def list(self, owner: User) -> list[Document]:
        result = await self._session.scalars(
            select(Document)
            .where(Document.owner_id == owner.id)
            .order_by(Document.created_at.desc())
        )
        return list(result)

    async def get(self, owner: User, document_id: uuid.UUID) -> Document:
        document = await self._session.scalar(
            select(Document).where(Document.id == document_id, Document.owner_id == owner.id)
        )
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def download_url(self, owner: User, document_id: uuid.UUID) -> str:
        document = await self.get(owner, document_id)
        return await self._storage.presigned_download_url(
            document.s3_key, document.filename, PRESIGNED_URL_TTL_SECONDS
        )

    async def delete(self, owner: User, document_id: uuid.UUID) -> None:
        document = await self.get(owner, document_id)
        # Remove derived data first; the DB row goes last so a failure can be retried.
        await self._vector_store.delete_document(document.id, document.chunk_count)
        await self._storage.delete(document.s3_key)
        await self._session.delete(document)
        await self._session.commit()
