"""Document endpoints: upload, list, get, download, delete."""

import uuid

from fastapi import APIRouter, BackgroundTasks, UploadFile, status

from app.api.deps import CurrentUser, DocumentServiceDep, IngestionDep
from app.schemas.document import DocumentResponse, DownloadUrlResponse
from app.services.document_service import PRESIGNED_URL_TTL_SECONDS

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile,
    user: CurrentUser,
    service: DocumentServiceDep,
    ingestion: IngestionDep,
    background_tasks: BackgroundTasks,
) -> DocumentResponse:
    """Store the file in S3 and start ingestion in the background.

    202 Accepted = "received, still processing": poll the document until
    `status` is `ready` (or `failed`).
    """
    document = await service.upload(user, file)
    # Runs after the response is sent. For heavy workloads use a real job queue
    # (Celery, arq, SQS + worker) so work survives restarts and scales separately.
    background_tasks.add_task(ingestion.run, document.id)
    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(user: CurrentUser, service: DocumentServiceDep) -> list[DocumentResponse]:
    return [DocumentResponse.model_validate(d) for d in await service.list(user)]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> DocumentResponse:
    return DocumentResponse.model_validate(await service.get(user, document_id))


@router.get("/{document_id}/download", response_model=DownloadUrlResponse)
async def download_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> DownloadUrlResponse:
    """Return a short-lived presigned S3 URL: the file never streams through our API."""
    url = await service.download_url(user, document_id)
    return DownloadUrlResponse(url=url, expires_in=PRESIGNED_URL_TTL_SECONDS)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: CurrentUser, service: DocumentServiceDep
) -> None:
    await service.delete(user, document_id)
