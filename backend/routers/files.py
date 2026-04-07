"""File serving endpoint — downloads files from MinIO."""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services import minio_client

log = structlog.get_logger()

router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_BUCKETS = {"uploads", "temp", "exports"}

# Content types served inline (displayed in browser)
INLINE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "application/pdf",
}

# Content types served as attachment (downloaded)
ATTACHMENT_TYPES = {
    "text/csv",
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
}


def _guess_content_type(path: str) -> str:
    """Guess the MIME type from file extension."""
    ct, _ = mimetypes.guess_type(path)
    return ct or "application/octet-stream"


def _content_disposition(path: str, content_type: str) -> str | None:
    """Return Content-Disposition header value, or None for inline."""
    if content_type in INLINE_TYPES:
        return None  # Browsers display inline by default
    filename = PurePosixPath(path).name
    return f'attachment; filename="{filename}"'


@router.get("/{path:path}")
def serve_file(path: str):
    """Serve a file from MinIO.

    Path format: {bucket}/{object_path}
    Example: uploads/00000000-.../conversation_id/file.pdf
    """
    # Split into bucket and object path
    parts = path.split("/", 1)
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="Invalid file path — expected {bucket}/{path}")

    bucket = parts[0]
    object_path = parts[1]

    if bucket not in ALLOWED_BUCKETS:
        raise HTTPException(status_code=400, detail=f"Invalid bucket: {bucket}")

    if ".." in object_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        data = minio_client.download_file(bucket, object_path)
    except Exception as exc:
        log.error("files.download.error", bucket=bucket, path=object_path, error=str(exc))
        raise HTTPException(status_code=404, detail="File not found")

    content_type = _guess_content_type(object_path)
    disposition = _content_disposition(object_path, content_type)

    headers = {}
    if disposition:
        headers["Content-Disposition"] = disposition

    log.info("files.served", bucket=bucket, path=object_path, content_type=content_type)

    return Response(
        content=data,
        media_type=content_type,
        headers=headers,
    )
