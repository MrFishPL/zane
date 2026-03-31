"""Web search-based SnapMagic/SnapEDA CAD availability checker.

Uses a LiteLLM endpoint to query an LLM (with optional web_search tool)
to determine whether a SnapMagic/SnapEDA product page exists for a given MPN.

When the real SnapMagic API becomes available, only this module needs to change.
The tool interface in server.py stays the same.
"""

import json
import os
import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm-proxy:4000")
MODEL = "gpt-4o-mini"
REQUEST_TIMEOUT = 30.0

# Canonical formats we recognise
KNOWN_FORMATS = {"kicad", "altium", "eagle"}


def _build_prompt(mpn: str) -> str:
    return (
        f'Search for "{mpn}" on snapeda.com or snapmagic.com. '
        f"Does a product page exist for this exact part number? "
        f"What CAD formats (KiCad, Altium, Eagle) are available for download? "
        f'Reply ONLY with JSON: {{"available": true/false, "url": "...", "formats": [...]}}'
    )


def _build_messages(mpn: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": _build_prompt(mpn)}]


def _build_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from LLM text output."""
    # First, try to find a JSON code block
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    # Then try to find a raw JSON object
    brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _normalise_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise an extracted JSON result into the canonical shape."""
    if raw is None:
        return {"available": False, "url": None, "formats": []}

    available = bool(raw.get("available", False))
    url = raw.get("url") or None
    if url and not isinstance(url, str):
        url = None

    raw_formats = raw.get("formats") or []
    if isinstance(raw_formats, str):
        raw_formats = [raw_formats]

    formats = [
        f.lower().strip()
        for f in raw_formats
        if isinstance(f, str) and f.lower().strip() in KNOWN_FORMATS
    ]

    return {"available": available, "url": url, "formats": formats}


class SnapMagicSearchClient:
    """Check SnapMagic/SnapEDA CAD model availability via LLM web search."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (base_url or LITELLM_BASE_URL).rstrip("/")
        self._model = model or MODEL
        self._timeout = timeout or REQUEST_TIMEOUT

    async def _call_llm_with_tools(self, mpn: str) -> dict[str, Any]:
        """Call LLM with web_search_options for real web access."""
        payload = {
            "model": self._model,
            "messages": _build_messages(mpn),
            "web_search_options": {"search_context_size": "medium"},
            "temperature": 0,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def _call_llm_simple(self, mpn: str) -> dict[str, Any]:
        """Fallback: call LLM without web search (uses training data only)."""
        payload = {
            "model": self._model,
            "messages": _build_messages(mpn),
            "temperature": 0,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    def _parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Extract the structured result from an LLM response."""
        choices = data.get("choices") or []
        if not choices:
            log.warning("search_client.no_choices")
            return _normalise_result(None)

        message = choices[0].get("message", {})
        content = message.get("content") or ""

        raw = _extract_json(content)
        return _normalise_result(raw)

    async def check_availability(self, mpn: str) -> dict[str, Any]:
        """Check if CAD models exist on SnapMagic/SnapEDA for the given MPN.

        Returns:
            {
                "available": bool,
                "url": str | None,
                "formats": list[str],   # e.g. ["kicad", "altium", "eagle"]
                "confidence": "high" | "low",
                "mpn": str,
            }
        """
        log.info("search_client.check", mpn=mpn[:200])

        # Try with web_search tool first
        try:
            data = await self._call_llm_with_tools(mpn)
            result = self._parse_response(data)
            result["confidence"] = "high"
            result["mpn"] = mpn
            log.info(
                "search_client.check.ok",
                mpn=mpn[:200],
                available=result["available"],
                method="tools",
            )
            return result
        except httpx.HTTPStatusError as exc:
            # If tool call is not supported (e.g. 400/422), fall back
            if exc.response.status_code in (400, 422):
                log.info(
                    "search_client.tools_unsupported",
                    mpn=mpn[:200],
                    status=exc.response.status_code,
                )
            else:
                raise

        # Fallback: simple prompt without tools
        try:
            data = await self._call_llm_simple(mpn)
            result = self._parse_response(data)
            result["confidence"] = "low"
            result["mpn"] = mpn
            log.info(
                "search_client.check.ok",
                mpn=mpn[:200],
                available=result["available"],
                method="simple",
            )
            return result
        except Exception:
            log.error("search_client.check.error", mpn=mpn[:200], exc_info=True)
            raise

    async def check_batch(
        self, mpns: list[str]
    ) -> list[dict[str, Any]]:
        """Check availability for multiple MPNs sequentially.

        Returns a list of results, one per MPN. Failed lookups include
        an ``error`` key instead of raising.
        """
        log.info("search_client.check_batch", count=len(mpns))
        results: list[dict[str, Any]] = []

        for mpn in mpns:
            try:
                result = await self.check_availability(mpn)
                results.append(result)
            except Exception as exc:
                log.warning(
                    "search_client.check_batch.mpn_error",
                    mpn=mpn[:200],
                    error=str(exc)[:200],
                )
                results.append(
                    {
                        "available": False,
                        "url": None,
                        "formats": [],
                        "confidence": "low",
                        "mpn": mpn,
                        "error": str(exc),
                    }
                )

        return results
