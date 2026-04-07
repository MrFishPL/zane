"""MCP server for BOM export generation (CSV, KiCad, Altium)."""

import time
from datetime import datetime, timezone
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from minio_client import MinIOClient
import csv_generator
import kicad_generator
import altium_generator

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

mcp = FastMCP("mcp-export", host="0.0.0.0", port=8005)

_minio: MinIOClient | None = None


def _get_minio() -> MinIOClient:
    """Lazy-initialise the MinIO client."""
    global _minio
    if _minio is None:
        _minio = MinIOClient()
    return _minio


def _log_tool_call(
    tool: str,
    params: str,
    duration_ms: int,
    success: bool,
    error: str | None = None,
) -> None:
    """Log a tool invocation with standard fields."""
    fields: dict[str, Any] = {
        "tool": tool,
        "params": params[:200],
        "duration_ms": duration_ms,
        "success": success,
    }
    if error:
        fields["error"] = error[:200]
    if success:
        log.info("tool_call", **fields)
    else:
        log.error("tool_call", **fields)


def _today_iso() -> str:
    """Return today's date in ISO format (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@mcp.tool()
async def generate_csv(
    components: list[dict],
    volume: int,
    user_id: str,
    conversation_id: str,
) -> dict:
    """Generate a CSV BOM file and upload to MinIO.

    Args:
        components: List of component dicts with mpn, qty_per_unit, etc.
        volume: Production volume multiplier for quantities.
        user_id: User ID for storage path.
        conversation_id: Conversation ID for storage path.

    Returns:
        Dict with ``path`` (MinIO URI) on success, or ``error`` on failure.
    """
    start = time.monotonic()
    params_str = f"components={len(components)}, volume={volume}"
    try:
        csv_content = csv_generator.generate(components, volume)
        csv_bytes = csv_content.encode("utf-8")

        minio = _get_minio()
        object_path = f"{user_id}/{conversation_id}/bom_{_today_iso()}.csv"
        uri = minio.upload_file("exports", object_path, csv_bytes, content_type="text/csv")

        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("generate_csv", params_str, duration_ms, True)
        return {"path": uri}
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("generate_csv", params_str, duration_ms, False, str(exc))
        return {"error": str(exc)}


@mcp.tool()
async def generate_kicad_library(
    components: list[dict],
    user_id: str,
    conversation_id: str,
) -> dict:
    """Generate a KiCad library ZIP and upload to MinIO.

    Args:
        components: List of component dicts with mpn, description, datasheet_url, etc.
        user_id: User ID for storage path.
        conversation_id: Conversation ID for storage path.

    Returns:
        Dict with ``path`` (MinIO URI) on success, or ``error`` on failure.
    """
    start = time.monotonic()
    params_str = f"components={len(components)}"
    try:
        zip_bytes = kicad_generator.generate_library(components)

        minio = _get_minio()
        object_path = f"{user_id}/{conversation_id}/kicad_library_{_today_iso()}.zip"
        uri = minio.upload_file(
            "exports", object_path, zip_bytes, content_type="application/zip"
        )

        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("generate_kicad_library", params_str, duration_ms, True)
        return {"path": uri}
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("generate_kicad_library", params_str, duration_ms, False, str(exc))
        return {"error": str(exc)}


@mcp.tool()
async def generate_altium_library(
    components: list[dict],
    user_id: str,
    conversation_id: str,
) -> dict:
    """Generate an Altium library ZIP and upload to MinIO.

    Args:
        components: List of component dicts with mpn, description, etc.
        user_id: User ID for storage path.
        conversation_id: Conversation ID for storage path.

    Returns:
        Dict with ``path`` (MinIO URI) on success, or ``error`` on failure.
    """
    start = time.monotonic()
    params_str = f"components={len(components)}"
    try:
        zip_bytes = altium_generator.generate_library(components)

        minio = _get_minio()
        object_path = f"{user_id}/{conversation_id}/altium_library_{_today_iso()}.zip"
        uri = minio.upload_file(
            "exports", object_path, zip_bytes, content_type="application/zip"
        )

        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("generate_altium_library", params_str, duration_ms, True)
        return {"path": uri}
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("generate_altium_library", params_str, duration_ms, False, str(exc))
        return {"error": str(exc)}


# Health endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-export"})


if __name__ == "__main__":
    log.info("mcp_export.starting", port=8005)
    mcp.run(transport="sse")
