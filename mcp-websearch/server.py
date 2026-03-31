"""MCP server for web search fallback component lookups."""

from __future__ import annotations

import structlog
from mcp.server.fastmcp import FastMCP

import search_client

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

mcp = FastMCP("mcp-websearch")


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Health check endpoint."""
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "service": "mcp-websearch"})


@mcp.tool()
async def search_distributor(query: str, site: str) -> dict:
    """Search a specific distributor site for electronic components.

    Uses LLM-powered web search to find components on distributor websites.

    Args:
        query: Component search query (e.g. "STM32F103C8T6", "100nF 0402 capacitor").
        site: Distributor domain to search. Supported sites:
              mouser.com, digikey.com, lcsc.com, tme.eu, farnell.com.

    Returns:
        Dict with "results" list. Each result contains: mpn, manufacturer,
        description, price, stock, url, mpn_confidence ("searched").
    """
    return await search_client.search_distributor(query, site)


@mcp.tool()
async def fetch_product_page(url: str) -> dict:
    """Fetch and extract structured product info from a distributor page URL.

    Analyzes a product page to extract pricing, stock availability, MPN,
    and description.

    Args:
        url: Full URL to a component product page on a distributor site.

    Returns:
        Dict with: mpn, manufacturer, description, price, stock, url, specs,
        mpn_confidence ("searched").
    """
    return await search_client.fetch_product_page(url)


if __name__ == "__main__":
    log.info("server_starting", service="mcp-websearch", port=8004)
    mcp.run(transport="sse", host="0.0.0.0", port=8004)
