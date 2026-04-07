"""MCP server for TME electronic component search."""

import asyncio as _asyncio
import atexit
import os
import time
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from tme_client import TMEClient

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

mcp = FastMCP("mcp-tme", host="0.0.0.0", port=8001)

client = TMEClient(
    token=os.environ.get("TME_APP_TOKEN", ""),
    app_secret=os.environ.get("TME_APP_SECRET", ""),
)


def _shutdown():
    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(client.close())
        else:
            loop.run_until_complete(client.close())
    except Exception:
        pass


atexit.register(_shutdown)


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
    """Search for electronic components by description (e.g. '100nF 0402 capacitor'). Returns top 5 results with pricing, stock, and TME links."""
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
    """Search for a specific component by Manufacturer Part Number or TME symbol. Returns detailed pricing and stock."""
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
async def search_parts_in_category(query: str, category_id: str) -> dict:
    """Search for components within a specific TME category. Use get_categories first to find the right category ID."""
    start = time.monotonic()
    try:
        result = await client.search_parts_in_category(query, category_id)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("search_parts_in_category", f"query={query}, cat={category_id}", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("search_parts_in_category", f"query={query}, cat={category_id}", duration_ms, False, str(exc))
        return {"error": str(exc), "results": []}


@mcp.tool()
async def get_categories(parent_id: int | None = None) -> dict:
    """Get TME category tree. Pass parent_id to get subcategories of a specific category. Returns category IDs, names, and product counts."""
    start = time.monotonic()
    try:
        result = await client.get_categories(parent_id)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_categories", f"parent_id={parent_id}", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_categories", f"parent_id={parent_id}", duration_ms, False, str(exc))
        return {"error": str(exc)}


@mcp.tool()
async def get_product_details(symbols: list[str]) -> dict:
    """Get detailed technical parameters for specific TME product symbols. Returns resistance, capacitance, package, voltage, etc."""
    start = time.monotonic()
    symbols_str = ", ".join(symbols[:5])
    try:
        result = await client.get_parameters(symbols)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_product_details", f"symbols=[{symbols_str}]", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_product_details", f"symbols=[{symbols_str}]", duration_ms, False, str(exc))
        return {"error": str(exc)}


@mcp.tool()
async def get_similar_products(symbols: list[str]) -> dict:
    """Find alternative/similar products for given TME symbols. Useful when a specific part is unavailable or too expensive."""
    start = time.monotonic()
    symbols_str = ", ".join(symbols[:5])
    try:
        result = await client.get_similar_products(symbols)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_similar_products", f"symbols=[{symbols_str}]", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("get_similar_products", f"symbols=[{symbols_str}]", duration_ms, False, str(exc))
        return {"error": str(exc)}


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


# Health endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp-tme"})


if __name__ == "__main__":
    log.info("mcp_tme.starting", port=8001)
    mcp.run(transport="sse")
