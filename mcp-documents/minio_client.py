"""MinIO client wrapper for document/image storage."""

from __future__ import annotations

import io
import os
from typing import Optional

import structlog
from minio import Minio
from minio.error import S3Error

log = structlog.get_logger()

AUTO_CREATE_BUCKETS = ["uploads", "temp", "exports"]


def parse_minio_uri(uri: str) -> tuple[str, str]:
    """Parse minio://bucket/path URI into (bucket, object_path).

    Args:
        uri: A MinIO URI like "minio://bucket/some/path/file.pdf"

    Returns:
        Tuple of (bucket_name, object_path).

    Raises:
        ValueError: If the URI is not a valid minio:// URI.
    """
    if not uri.startswith("minio://"):
        raise ValueError(f"Invalid MinIO URI (must start with minio://): {uri}")
    stripped = uri[len("minio://"):]
    parts = stripped.split("/", 1)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid MinIO URI (expected minio://bucket/path): {uri}")
    return parts[0], parts[1]


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
        try:
            response = self._client.get_object(bucket, path)
            data = response.read()
            return data
        except S3Error as exc:
            log.error("file_download_failed", bucket=bucket, path=path, error=str(exc))
            raise FileNotFoundError(f"Object not found: minio://{bucket}/{path}") from exc
        finally:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass

    def list_files(self, bucket: str, prefix: str = "") -> list[str]:
        """List object paths in a bucket under the given prefix."""
        try:
            objects = self._client.list_objects(bucket, prefix=prefix, recursive=True)
            return [obj.object_name for obj in objects]
        except S3Error as exc:
            log.error("list_files_failed", bucket=bucket, prefix=prefix, error=str(exc))
            return []

    def delete_files(self, bucket: str, prefix: str = "") -> int:
        """Delete all objects under a prefix. Returns count of deleted objects."""
        from minio.deleteobjects import DeleteObject

        objects = self.list_files(bucket, prefix)
        if not objects:
            return 0

        delete_list = [DeleteObject(name) for name in objects]
        errors = list(self._client.remove_objects(bucket, delete_list))
        deleted = len(objects) - len(errors)
        if errors:
            log.warning("delete_files_partial", bucket=bucket, prefix=prefix, errors=len(errors))
        else:
            log.info("files_deleted", bucket=bucket, prefix=prefix, count=deleted)
        return deleted
