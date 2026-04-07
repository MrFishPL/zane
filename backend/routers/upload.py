"""File upload endpoint."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from services import minio_client
from config import DEFAULT_USER_ID

log = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["upload"])

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
):
    """Upload a file to MinIO.

    If conversation_id is provided, uploads directly to the conversation path.
    Otherwise, uploads to a staging area with a unique upload_id.
    """
    # Validate MIME type
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        log.warning("upload.invalid_mime", content_type=content_type, filename=file.filename)
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type}. Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}",
        )

    # Read file content with streaming size check
    chunks = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB chunks
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_FILE_SIZE:
            log.warning("upload.too_large", size=size, filename=file.filename)
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum: {MAX_FILE_SIZE} bytes (100MB)",
            )
        chunks.append(chunk)

    data = b"".join(chunks)

    upload_id = str(uuid.uuid4())
    filename = file.filename or upload_id

    # Determine upload path
    if conversation_id:
        path = f"{DEFAULT_USER_ID}/{conversation_id}/{filename}"
    else:
        path = f"{DEFAULT_USER_ID}/staging/{upload_id}/{filename}"

    # Upload to MinIO
    full_path = minio_client.upload_file(
        bucket="uploads",
        path=path,
        data=data,
        content_type=content_type,
        size=size,
    )

    log.info(
        "upload.success",
        path=full_path,
        upload_id=upload_id,
        size=size,
        content_type=content_type,
    )

    # Strip minio:// prefix so paths are clean for the frontend
    clean_path = full_path.removeprefix("minio://")

    return {
        "path": clean_path,
        "upload_id": upload_id,
        "filename": filename,
        "size": size,
        "content_type": content_type,
    }
