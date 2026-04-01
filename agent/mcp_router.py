"""Routes OpenAI tool calls to the appropriate MCP server.

Maintains persistent SSE connections to each MCP server and
dispatches tool invocations by name.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog
from mcp import ClientSession
from mcp.client.sse import sse_client

log = structlog.get_logger()

# ---- tool-name -> (server_key, server_url) mapping ----

_TOOL_SERVER_MAP: dict[str, str] = {
    # mcp-documents
    "render_pdf_pages": "mcp-documents",
    "get_image_base64": "mcp-documents",
    "crop_zoom_image": "mcp-documents",
    "extract_text": "mcp-documents",
    # mcp-nexar
    "search_parts": "mcp-nexar",
    "search_mpn": "mcp-nexar",
    "get_part_details": "mcp-nexar",
    "check_stock": "mcp-nexar",
    # mcp-snapmagic
    "check_cad_availability": "mcp-snapmagic",
    "check_cad_batch": "mcp-snapmagic",
    # mcp-websearch
    "search_distributor": "mcp-websearch",
    "fetch_product_page": "mcp-websearch",
    # mcp-export
    "generate_csv": "mcp-export",
    "generate_kicad_library": "mcp-export",
    "generate_altium_library": "mcp-export",
}

_DEFAULT_SERVERS: dict[str, str] = {
    "mcp-documents": "http://mcp-documents:8003",
    "mcp-nexar": "http://mcp-nexar:8001",
    "mcp-snapmagic": "http://mcp-snapmagic:8002",
    "mcp-websearch": "http://mcp-websearch:8004",
    "mcp-export": "http://mcp-export:8005",
}


class MCPRouter:
    """Dispatches tool calls to the correct MCP server."""

    def __init__(self, server_urls: dict[str, str] | None = None) -> None:
        self._server_urls = server_urls or dict(_DEFAULT_SERVERS)
        self._sessions: dict[str, tuple[ClientSession, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {
            key: asyncio.Lock() for key in self._server_urls
        }
        self._http = httpx.AsyncClient(timeout=30.0)

    # -- lifecycle --

    async def close(self) -> None:
        """Release all held connections."""
        for key, (session, _ctx) in list(self._sessions.items()):
            try:
                # ClientSession does not expose a close(); its context
                # manager is handled by the sse_client context.  We just
                # drop the reference so the GC can collect it.
                pass
            except Exception:
                log.warning("mcp_session_close_error", server=key, exc_info=True)
        self._sessions.clear()
        await self._http.aclose()

    # -- public API --

    def server_for_tool(self, tool_name: str) -> str | None:
        """Return the server key that owns *tool_name*, or None."""
        return _TOOL_SERVER_MAP.get(tool_name)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        """Call *tool_name* on the owning MCP server and return the result.

        Falls back to a plain HTTP POST to ``/mcp/call_tool`` if SSE
        connection fails.
        """
        server_key = _TOOL_SERVER_MAP.get(tool_name)
        if server_key is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        base_url = self._server_urls[server_key]

        log.info(
            "mcp_tool_call",
            tool=tool_name,
            server=server_key,
            url=base_url,
            arguments=arguments,
        )

        # Try SSE-based MCP client first
        try:
            result = await self._call_via_sse(server_key, base_url, tool_name, arguments)
            log.info("mcp_tool_result", tool=tool_name, server=server_key, success=True)
            return result
        except Exception as exc:
            log.warning(
                "mcp_sse_failed_falling_back_to_http",
                tool=tool_name,
                server=server_key,
                error=str(exc),
            )

        # Fallback: plain HTTP POST
        return await self._call_via_http(base_url, tool_name, arguments)

    # -- internal transport layers --

    async def _call_via_sse(
        self,
        server_key: str,
        base_url: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Open (or reuse) an SSE session and invoke the tool."""
        sse_url = f"{base_url}/sse"

        # Each call opens a fresh short-lived session.  MCP SSE
        # transport is designed around per-request streams, so keeping a
        # long-lived session is unreliable across reconnects.
        async with sse_client(sse_url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return self._extract_content(result)

    async def _call_via_http(
        self,
        base_url: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Fallback: POST a JSON-RPC-style call to the server."""
        url = f"{base_url}/mcp/call_tool"
        payload = {"name": tool_name, "arguments": arguments}

        resp = await self._http.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()

        log.info("mcp_http_result", tool=tool_name, status=resp.status_code)
        return body.get("result", body)

    # -- helpers --

    @staticmethod
    def _extract_content(result: Any) -> Any:
        """Pull text/json content out of an MCP CallToolResult."""
        if hasattr(result, "content"):
            parts = result.content
            if len(parts) == 1:
                part = parts[0]
                if hasattr(part, "text"):
                    return part.text
                return part
            return [p.text if hasattr(p, "text") else p for p in parts]
        return result
