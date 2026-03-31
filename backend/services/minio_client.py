"""MinIO object storage client wrapper."""

from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone

import structlog
from minio import Minio
from minio.deleteobjects import DeleteObject

log = structlog.get_logger()

REQUIRED_BUCKETS = ["uploads", "temp", "exports"]

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        raise RuntimeError("MinIO client not initialised — call init() first")
    return _client


def init() -> Minio:
    """Initialise MinIO client and auto-create required buckets."""
    global _client
    endpoint = os.environ["MINIO_ENDPOINT"]
    access_key = os.environ["MINIO_ROOT_USER"]
    secret_key = os.environ["MINIO_ROOT_PASSWORD"]

    _client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
    log.info("minio.initialised", endpoint=endpoint)

    # Auto-create buckets
    for bucket in REQUIRED_BUCKETS:
        if not _client.bucket_exists(bucket):
            _client.make_bucket(bucket)
            log.info("minio.bucket.created", bucket=bucket)
        else:
            log.info("minio.bucket.exists", bucket=bucket)

    return _client


def upload_file(
    bucket: str,
    path: str,
    data: bytes,
    content_type: str,
    size: int,
) -> str:
    """Upload a file and return the full MinIO path."""
    get_client().put_object(
        bucket,
        path,
        io.BytesIO(data),
        length=size,
        content_type=content_type,
    )
    full_path = f"minio://{bucket}/{path}"
    log.info("minio.file.uploaded", bucket=bucket, path=path, size=size)
    return full_path


def download_file(bucket: str, path: str) -> bytes:
    """Download a file and return its bytes."""
    response = get_client().get_object(bucket, path)
    try:
        data = response.read()
    finally:
        response.close()
        response.release_conn()
    log.info("minio.file.downloaded", bucket=bucket, path=path, size=len(data))
    return data


def delete_prefix(bucket: str, prefix: str) -> None:
    """Delete all objects under a prefix."""
    client = get_client()
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    delete_list = [DeleteObject(obj.object_name) for obj in objects]
    if delete_list:
        errors = list(client.remove_objects(bucket, delete_list))
        if errors:
            for err in errors:
                log.error("minio.delete.error", error=str(err))
        log.info("minio.prefix.deleted", bucket=bucket, prefix=prefix, count=len(delete_list))


def move_files(
    src_bucket: str,
    src_prefix: str,
    dst_bucket: str,
    dst_prefix: str,
) -> list[str]:
    """Move files from source to destination (copy + delete). Returns list of new paths."""
    client = get_client()
    moved: list[str] = []
    objects = list(client.list_objects(src_bucket, prefix=src_prefix, recursive=True))

    for obj in objects:
        # Compute destination path
        relative = obj.object_name[len(src_prefix):].lstrip("/")
        dst_path = f"{dst_prefix}/{relative}" if relative else dst_prefix

        # Copy
        from minio.commonconfig import CopySource
        client.copy_object(
            dst_bucket,
            dst_path,
            CopySource(src_bucket, obj.object_name),
        )
        # Delete original
        client.remove_object(src_bucket, obj.object_name)
        moved.append(f"minio://{dst_bucket}/{dst_path}")

    log.info(
        "minio.files.moved",
        src=f"{src_bucket}/{src_prefix}",
        dst=f"{dst_bucket}/{dst_prefix}",
        count=len(moved),
    )
    return moved


def list_files(bucket: str, prefix: str) -> list[dict]:
    """List files under a prefix, returning metadata dicts."""
    client = get_client()
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    files = []
    for obj in objects:
        files.append({
            "name": obj.object_name,
            "size": obj.size,
            "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
            "content_type": obj.content_type,
        })
    return files


def cleanup_staging(max_age_hours: int = 24) -> int:
    """Delete staging files older than max_age_hours. Returns count deleted."""
    client = get_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    deleted = 0

    objects = list(client.list_objects("uploads", prefix="", recursive=True))
    staging_objects = [o for o in objects if "/staging/" in o.object_name]

    delete_list = []
    for obj in staging_objects:
        if obj.last_modified and obj.last_modified < cutoff:
            delete_list.append(DeleteObject(obj.object_name))

    if delete_list:
        errors = list(client.remove_objects("uploads", delete_list))
        deleted = len(delete_list) - len(errors)
        if errors:
            for err in errors:
                log.error("minio.staging.cleanup.error", error=str(err))

    log.info("minio.staging.cleanup", deleted=deleted, checked=len(staging_objects))
    return deleted
