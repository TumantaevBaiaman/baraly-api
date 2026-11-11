import asyncio
import io
import json
import logging

import urllib3
from minio import Minio
from minio.error import MinioException

from app.application.exceptions import ExternalServiceError
from app.config import Settings

logger = logging.getLogger(__name__)


def create_minio_client(settings: Settings) -> Minio:
    # Короткие таймауты и мало повторов: недоступное хранилище не должно вешать запрос.
    http_client = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=3, read=15),
        retries=urllib3.Retry(total=2, backoff_factor=0.2),
    )
    return Minio(
        settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key.get_secret_value(),
        secure=settings.s3_secure,
        region=settings.s3_region,
        http_client=http_client,
    )


class S3ImageStorage:
    """Хранит картинки в S3-совместимом хранилище (MinIO) с публичным чтением."""

    def __init__(self, client: Minio, bucket: str, public_url: str) -> None:
        self._client = client
        self._bucket = bucket
        self._public_url = public_url.rstrip("/")
        self._bucket_ready = False
        self._lock = asyncio.Lock()

    async def save(self, key: str, data: bytes, content_type: str) -> str:
        try:
            await self._ensure_bucket()
            await asyncio.to_thread(
                self._client.put_object,
                self._bucket,
                key,
                io.BytesIO(data),
                len(data),
                content_type=content_type,
            )
        except (MinioException, urllib3.exceptions.HTTPError, OSError) as exc:
            logger.warning("Failed to store %s", key, exc_info=True)
            raise ExternalServiceError("Image storage is unavailable") from exc
        return self.public_url(key)

    def public_url(self, key: str) -> str:
        return f"{self._public_url}/{self._bucket}/{key}"

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        async with self._lock:
            if not self._bucket_ready:
                await asyncio.to_thread(self._create_public_bucket)
                self._bucket_ready = True

    def _create_public_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{self._bucket}/*"],
                }
            ],
        }
        self._client.set_bucket_policy(self._bucket, json.dumps(policy))
