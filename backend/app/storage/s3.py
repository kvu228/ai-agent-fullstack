"""Blob storage on S3 (Floci locally, AWS S3 in production).

boto3 is synchronous, so every call runs in a worker thread via `asyncio.to_thread`
to keep the event loop free.
"""

import asyncio
import logging
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _make_client(settings: Settings, endpoint_url: str | None) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.aws_region,
        # None -> boto3's default chain (env vars, IAM role, ...): what you want on AWS.
        aws_access_key_id=(
            settings.aws_access_key_id.get_secret_value() if settings.aws_access_key_id else None
        ),
        aws_secret_access_key=(
            settings.aws_secret_access_key.get_secret_value()
            if settings.aws_secret_access_key
            else None
        ),
        # Path-style URLs (http://host/bucket/key) work with local emulators.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client: Any = _make_client(settings, settings.s3_endpoint_url)
        # Presigned URLs are opened by the browser, which may need a different host.
        public_endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url
        self._public_client: Any = _make_client(settings, public_endpoint)

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                logger.info("Creating bucket %s", self._bucket)
                self._client.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(_ensure)

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def download(self, key: str) -> bytes:
        def _download() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        return await asyncio.to_thread(_download)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def presigned_download_url(self, key: str, filename: str, expires_in: int = 300) -> str:
        """A short-lived URL that lets the browser download the file directly from S3."""
        url: str = await asyncio.to_thread(
            self._public_client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=expires_in,
        )
        return url
