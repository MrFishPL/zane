"""MCP server for Nexar/Octopart electronic component search."""

import os
import time
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from nexar_client import NexarClient

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

mcp = FastMCP("mcp-nexar", host="0.0.0.0", port=8001)

client = NexarClient(
    client_id=os.environ.get("NEXAR_CLIENT_ID", ""),
    client_secret=os.environ.get("NEXAR_CLIENT_SECRET", ""),
)


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


@mcp.tool()
async def search_parts(query: str) -> dict:
    """Search for electronic components by description (e.g. '3 ohm resistor 0603'). Returns top 5 results with specs, pricing, stock, and distributor links."""
    start = time.monotonic()
    try:
        result = await client.search_parts(query)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("search_parts", f"query={query}", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("search_parts", f"query={query}", duration_ms, False, str(exc))
        return {"error": str(exc), "results": []}


@mcp.tool()
async def search_mpn(mpn: str) -> dict:
    """Search for a specific component by Manufacturer Part Number. Returns detailed pricing, stock, and specs."""
    start = time.monotonic()
    try:
        result = await client.search_mpn(mpn)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("search_mpn", f"mpn={mpn}", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("search_mpn", f"mpn={mpn}", duration_ms, False, str(exc))
        return {"error": str(exc), "results": []}


@mcp.tool()
async def multi_match(mpns: list[str]) -> dict:
    """Batch lookup of multiple MPNs at once. Returns results for each MPN."""
    start = time.monotonic()
    mpns_str = ", ".join(mpns[:10])
    try:
        result = await client.multi_match(mpns)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("multi_match", f"mpns=[{mpns_str}]", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("multi_match", f"mpns=[{mpns_str}]", duration_ms, False, str(exc))
        return {"error": str(exc), "results": {}, "errors": {}}


@mcp.tool()
async def check_lifecycle(mpn: str) -> dict:
    """Check lifecycle status of a component (active/nrnd/obsolete/unknown)."""
    start = time.monotonic()
    try:
        result = await client.check_lifecycle(mpn)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("check_lifecycle", f"mpn={mpn}", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("check_lifecycle", f"mpn={mpn}", duration_ms, False, str(exc))
        return {"error": str(exc), "mpn": mpn, "lifecycle": "unknown"}


@mcp.tool()
async def get_quota_status() -> dict:
    """Get remaining Nexar API quota for this month."""
    start = time.monotonic()
    try:
        result = await client.get_quota_status()
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_quota_status", "", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_quota_status", "", duration_ms, False, str(exc))
        return {"error": str(exc), "status": "error"}


# Health endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-nexar"})


if __name__ == "__main__":
    log.info("mcp_nexar.starting", port=8001)
    mcp.run(transport="sse")
