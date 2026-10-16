import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    error: str | None
    created_at: datetime
    # Note: `s3_key` and `owner_id` are deliberately NOT exposed.


class DownloadUrlResponse(BaseModel):
    url: str
    expires_in: int
