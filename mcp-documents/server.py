"""MCP server for document/image processing tools."""

from __future__ import annotations

import json
import time
from pathlib import PurePosixPath

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from image_processor import annotate, crop_zoom, get_info, to_base64
from minio_client import MinIOClient, parse_minio_uri
from pdf_processor import (
    classify_page as _classify_page,
    extract_text as _extract_text,
    render_all_pages,
    render_page as _render_page,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()

mcp = FastMCP("mcp-documents")
_minio: MinIOClient | None = None


def get_minio() -> MinIOClient:
    """Lazy-initialize the MinIO client singleton."""
    global _minio
    if _minio is None:
        _minio = MinIOClient()
    return _minio


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate a string for logging."""
    return s[:max_len] + "..." if len(s) > max_len else s


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def render_pdf_pages(pdf_path: str) -> str:
    """Render all pages of a PDF to PNG images.

    Downloads the PDF from MinIO, renders every page at 300 DPI,
    uploads PNGs back to MinIO temp storage, and returns a JSON manifest.

    Args:
        pdf_path: MinIO URI of the PDF (minio://bucket/path/file.pdf).

    Returns:
        JSON manifest with pages list: number, classification, minio_path.
    """
    start = time.time()
    log.info("tool_call", tool="render_pdf_pages", params=_truncate(pdf_path))
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(pdf_path)
        pdf_bytes = client.download_file(bucket, path)

        pages = render_all_pages(pdf_bytes)
        stem = PurePosixPath(path).stem

        manifest = {"pages": []}
        for page_num, png_data in pages:
            temp_path = f"{stem}/page_{page_num}.png"
            minio_path = client.upload_file("temp", temp_path, png_data, "image/png")
            classification = _classify_page(pdf_bytes, page_num)
            manifest["pages"].append({
                "number": page_num,
                "classification": classification,
                "minio_path": minio_path,
            })

        duration = time.time() - start
        log.info("tool_success", tool="render_pdf_pages", duration=round(duration, 3), page_count=len(pages))
        return json.dumps(manifest)
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="render_pdf_pages", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def render_pdf_page(pdf_path: str, page_number: int) -> str:
    """Render a single page of a PDF to PNG at 300 DPI.

    Args:
        pdf_path: MinIO URI of the PDF.
        page_number: 1-based page number.

    Returns:
        JSON with minio_path of the rendered PNG.
    """
    start = time.time()
    log.info("tool_call", tool="render_pdf_page", params=_truncate(f"{pdf_path}, page={page_number}"))
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(pdf_path)
        pdf_bytes = client.download_file(bucket, path)

        png_data = _render_page(pdf_bytes, page_number)
        stem = PurePosixPath(path).stem
        temp_path = f"{stem}/page_{page_number}.png"
        minio_path = client.upload_file("temp", temp_path, png_data, "image/png")

        duration = time.time() - start
        log.info("tool_success", tool="render_pdf_page", duration=round(duration, 3))
        return json.dumps({"minio_path": minio_path})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="render_pdf_page", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def classify_page(pdf_path: str, page_number: int) -> str:
    """Classify a PDF page as 'schematic' or 'text'.

    Args:
        pdf_path: MinIO URI of the PDF.
        page_number: 1-based page number.

    Returns:
        JSON with classification: 'schematic' or 'text'.
    """
    start = time.time()
    log.info("tool_call", tool="classify_page", params=_truncate(f"{pdf_path}, page={page_number}"))
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(pdf_path)
        pdf_bytes = client.download_file(bucket, path)

        classification = _classify_page(pdf_bytes, page_number)

        duration = time.time() - start
        log.info("tool_success", tool="classify_page", duration=round(duration, 3))
        return json.dumps({"classification": classification})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="classify_page", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def extract_text(pdf_path: str, page_number: int) -> str:
    """Extract native text from a PDF page (not OCR).

    Args:
        pdf_path: MinIO URI of the PDF.
        page_number: 1-based page number.

    Returns:
        JSON with extracted text content.
    """
    start = time.time()
    log.info("tool_call", tool="extract_text", params=_truncate(f"{pdf_path}, page={page_number}"))
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(pdf_path)
        pdf_bytes = client.download_file(bucket, path)

        text = _extract_text(pdf_bytes, page_number)

        duration = time.time() - start
        log.info("tool_success", tool="extract_text", duration=round(duration, 3))
        return json.dumps({"text": text})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="extract_text", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_image_base64(image_path: str) -> str:
    """Download an image from MinIO and return it as base64.

    This is how the agent sends images to the LLM for vision analysis.

    Args:
        image_path: MinIO URI of the image.

    Returns:
        JSON with base64 string of the image.
    """
    start = time.time()
    log.info("tool_call", tool="get_image_base64", params=_truncate(image_path))
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(image_path)
        image_bytes = client.download_file(bucket, path)

        b64 = to_base64(image_bytes)

        duration = time.time() - start
        log.info("tool_success", tool="get_image_base64", duration=round(duration, 3))
        return json.dumps({"base64": b64})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="get_image_base64", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def crop_zoom_image(
    image_path: str,
    x1_pct: float,
    y1_pct: float,
    x2_pct: float,
    y2_pct: float,
) -> str:
    """Crop a region of an image and upscale for detailed inspection.

    Coordinates are percentages (0-100) of the image dimensions.
    Saves the cropped image to MinIO temp and returns base64 for the agent.

    Args:
        image_path: MinIO URI of the source image.
        x1_pct: Left edge percentage (0-100).
        y1_pct: Top edge percentage (0-100).
        x2_pct: Right edge percentage (0-100).
        y2_pct: Bottom edge percentage (0-100).

    Returns:
        JSON with base64 string and minio_path of cropped image.
    """
    start = time.time()
    log.info(
        "tool_call",
        tool="crop_zoom",
        params=_truncate(f"{image_path}, ({x1_pct},{y1_pct})-({x2_pct},{y2_pct})"),
    )
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(image_path)
        image_bytes = client.download_file(bucket, path)

        cropped_bytes = crop_zoom(image_bytes, x1_pct, y1_pct, x2_pct, y2_pct)
        b64 = to_base64(cropped_bytes)

        stem = PurePosixPath(path).stem
        suffix = PurePosixPath(path).suffix or ".png"
        temp_path = f"crops/{stem}_crop_{int(x1_pct)}_{int(y1_pct)}_{int(x2_pct)}_{int(y2_pct)}{suffix}"
        minio_path = client.upload_file("temp", temp_path, cropped_bytes, "image/png")

        duration = time.time() - start
        log.info("tool_success", tool="crop_zoom", duration=round(duration, 3))
        return json.dumps({"base64": b64, "minio_path": minio_path})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="crop_zoom", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def annotate_image(image_path: str, rectangles: list[dict]) -> str:
    """Draw red rectangles with labels on an image.

    Each rectangle must have: x1, y1, x2, y2 (pixel coords), label (string).
    Saves annotated image to MinIO and returns the minio_path for frontend display.

    Args:
        image_path: MinIO URI of the source image.
        rectangles: List of rectangle dicts with x1, y1, x2, y2, label.

    Returns:
        JSON with minio_path of the annotated image.
    """
    start = time.time()
    log.info(
        "tool_call",
        tool="annotate_image",
        params=_truncate(f"{image_path}, rects={len(rectangles)}"),
    )
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(image_path)
        image_bytes = client.download_file(bucket, path)

        annotated_bytes = annotate(image_bytes, rectangles)

        stem = PurePosixPath(path).stem
        temp_path = f"annotated/{stem}_annotated.png"
        minio_path = client.upload_file("temp", temp_path, annotated_bytes, "image/png")

        duration = time.time() - start
        log.info("tool_success", tool="annotate_image", duration=round(duration, 3))
        return json.dumps({"minio_path": minio_path})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="annotate_image", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_image_info(image_path: str) -> str:
    """Get dimensions, format, and file size of an image.

    Args:
        image_path: MinIO URI of the image.

    Returns:
        JSON with width, height, format, file_size.
    """
    start = time.time()
    log.info("tool_call", tool="get_image_info", params=_truncate(image_path))
    try:
        client = get_minio()
        bucket, path = parse_minio_uri(image_path)
        image_bytes = client.download_file(bucket, path)

        info = get_info(image_bytes)

        duration = time.time() - start
        log.info("tool_success", tool="get_image_info", duration=round(duration, 3))
        return json.dumps(info)
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="get_image_info", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_temp_files() -> str:
    """List all files in the MinIO temp bucket.

    Returns:
        JSON with list of file paths.
    """
    start = time.time()
    log.info("tool_call", tool="list_temp_files")
    try:
        client = get_minio()
        files = client.list_files("temp")

        duration = time.time() - start
        log.info("tool_success", tool="list_temp_files", duration=round(duration, 3), count=len(files))
        return json.dumps({"files": files})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="list_temp_files", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


@mcp.tool()
def cleanup_temp() -> str:
    """Delete all files in the MinIO temp bucket.

    Returns:
        JSON with count of deleted files.
    """
    start = time.time()
    log.info("tool_call", tool="cleanup_temp")
    try:
        client = get_minio()
        deleted = client.delete_files("temp")

        duration = time.time() - start
        log.info("tool_success", tool="cleanup_temp", duration=round(duration, 3), deleted=deleted)
        return json.dumps({"deleted": deleted})
    except Exception as exc:
        duration = time.time() - start
        log.error("tool_error", tool="cleanup_temp", error=str(exc), duration=round(duration, 3))
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse({"status": "ok", "service": "mcp-documents"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("server_starting", transport="sse", port=8003)
    mcp.run(transport="sse", host="0.0.0.0", port=8003)
