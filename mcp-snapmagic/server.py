"""MCP server for SnapMagic/SnapEDA CAD model availability checks."""

import os
import time
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from search_client import SnapMagicSearchClient

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

mcp = FastMCP("mcp-snapmagic", host="0.0.0.0", port=8002)

client = SnapMagicSearchClient(
    base_url=os.environ.get("LITELLM_BASE_URL"),
)

VALID_FORMATS = {"kicad", "altium", "eagle", "any"}


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


def _filter_formats(result: dict[str, Any], fmt: str) -> dict[str, Any]:
    """If a specific format was requested, adjust 'available' accordingly."""
    if fmt == "any":
        return result

    fmt_lower = fmt.lower()
    has_format = fmt_lower in result.get("formats", [])

    return {
        **result,
        "available": result.get("available", False) and has_format,
    }


@mcp.tool()
async def check_cad_availability(mpn: str, format: str = "any") -> dict:
    """Check if a symbol/footprint exists on SnapMagic for the given MPN.

    Args:
        mpn: Manufacturer Part Number to look up.
        format: CAD format to check: "kicad", "altium", "eagle", or "any".

    Returns:
        {available: bool, url: str | null, formats: list[str]}
    """
    fmt = format.lower().strip()
    if fmt not in VALID_FORMATS:
        return {
            "error": f"Invalid format '{format}'. Must be one of: kicad, altium, eagle, any",
            "available": False,
            "url": None,
            "formats": [],
        }

    start = time.monotonic()
    try:
        result = await client.check_availability(mpn)
        result = _filter_formats(result, fmt)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call(
            "check_cad_availability",
            f"mpn={mpn}, format={fmt}",
            duration_ms,
            True,
        )
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call(
            "check_cad_availability",
            f"mpn={mpn}, format={fmt}",
            duration_ms,
            False,
            str(exc),
        )
        return {
            "error": str(exc),
            "available": False,
            "url": None,
            "formats": [],
            "mpn": mpn,
        }


@mcp.tool()
async def check_cad_batch(mpns: list[str], format: str = "any") -> dict:
    """Batch lookup of CAD model availability for multiple MPNs.

    Args:
        mpns: List of Manufacturer Part Numbers to look up.
        format: CAD format to check: "kicad", "altium", "eagle", or "any".

    Returns:
        {results: list[{available: bool, url: str | null, formats: list[str]}]}
    """
    fmt = format.lower().strip()
    if fmt not in VALID_FORMATS:
        return {
            "error": f"Invalid format '{format}'. Must be one of: kicad, altium, eagle, any",
            "results": [],
        }

    start = time.monotonic()
    mpns_str = ", ".join(mpns[:10])
    try:
        results = await client.check_batch(mpns)
        results = [_filter_formats(r, fmt) for r in results]
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call(
            "check_cad_batch",
            f"mpns=[{mpns_str}], format={fmt}",
            duration_ms,
            True,
        )
        return {"results": results}
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call(
            "check_cad_batch",
            f"mpns=[{mpns_str}], format={fmt}",
            duration_ms,
            False,
            str(exc),
        )
        return {"error": str(exc), "results": []}


# Health endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-snapmagic"})


if __name__ == "__main__":
    log.info("mcp_snapmagic.starting", port=8002)
    mcp.run(transport="sse")
