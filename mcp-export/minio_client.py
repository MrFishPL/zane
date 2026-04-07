"""MinIO client wrapper for export file storage."""

from __future__ import annotations

import io
import os
from typing import Optional

import structlog
from minio import Minio
from minio.error import S3Error

log = structlog.get_logger()

AUTO_CREATE_BUCKETS = ["exports"]


class MinIOClient:
    """Wrapper around the Minio SDK for internal Docker-network access."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        self._endpoint = endpoint or os.environ.get("MINIO_ENDPOINT", "minio:9000")
        self._access_key = access_key or os.environ.get("MINIO_ROOT_USER", "minioadmin")
        self._secret_key = secret_key or os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")

        self._client = Minio(
            self._endpoint,
            access_key=self._access_key,
            secret_key=self._secret_key,
            secure=False,
        )
        self._ensure_buckets()

    def _ensure_buckets(self) -> None:
        """Auto-create required buckets if they don't exist."""
        for bucket in AUTO_CREATE_BUCKETS:
            try:
                if not self._client.bucket_exists(bucket):
                    self._client.make_bucket(bucket)
                    log.info("bucket_created", bucket=bucket)
            except S3Error as exc:
                log.error("bucket_create_failed", bucket=bucket, error=str(exc))

    def upload_file(
        self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """Upload bytes to MinIO and return the minio:// URI."""
        stream = io.BytesIO(data)
        self._client.put_object(
            bucket,
            path,
            stream,
            length=len(data),
            content_type=content_type,
        )
        uri = f"minio://{bucket}/{path}"
        log.info("file_uploaded", bucket=bucket, path=path, size=len(data))
        return uri

    def download_file(self, bucket: str, path: str) -> bytes:
        """Download an object from MinIO and return its bytes."""
        response = None
        try:
            response = self._client.get_object(bucket, path)
            data = response.read()
            return data
        except S3Error as exc:
            log.error("file_download_failed", bucket=bucket, path=path, error=str(exc))
            raise FileNotFoundError(f"Object not found: minio://{bucket}/{path}") from exc
        finally:
            if response is not None:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass
