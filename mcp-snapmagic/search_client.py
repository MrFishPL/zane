"""SnapMagic/SnapEDA CAD availability checker using Tavily web search.

Searches snapeda.com for each MPN to determine if CAD models
(schematic symbols, footprints) are available for download.
"""

import os
import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL = "https://api.tavily.com/search"
REQUEST_TIMEOUT = 15.0

KNOWN_FORMATS = {"kicad", "altium", "eagle"}


class SnapMagicSearchClient:
    """Check SnapMagic/SnapEDA CAD model availability via Tavily web search."""

    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        self._api_key = api_key or TAVILY_API_KEY
        self._timeout = timeout or REQUEST_TIMEOUT

    async def check_availability(self, mpn: str) -> dict[str, Any]:
        """Check if CAD models exist on SnapEDA for the given MPN."""
        log.info("search_client.check", mpn=mpn[:200])

        if not self._api_key:
            log.warning("search_client.no_api_key")
            return {"available": False, "url": None, "formats": [], "confidence": "low", "mpn": mpn}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    TAVILY_URL,
                    json={
                        "api_key": self._api_key,
                        "query": f"site:snapeda.com {mpn} CAD download",
                        "search_depth": "basic",
                        "max_results": 3,
                        "include_domains": ["snapeda.com", "snapmagic.com"],
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", [])
            snapeda_url = None
            available = False
            formats: list[str] = []

            for r in results:
                url = r.get("url", "")
                content = (r.get("content", "") + " " + r.get("title", "")).lower()

                # Check if this is a SnapEDA part page
                if "snapeda.com/parts/" in url or "snapmagic.com/parts/" in url:
                    available = True
                    snapeda_url = url
                    # Detect formats from page content
                    if "kicad" in content:
                        formats.append("kicad")
                    if "altium" in content:
                        formats.append("altium")
                    if "eagle" in content:
                        formats.append("eagle")
                    break

            result = {
                "available": available,
                "url": snapeda_url,
                "formats": sorted(set(formats)),
                "confidence": "high" if results else "low",
                "mpn": mpn,
            }

            log.info("search_client.check.ok", mpn=mpn[:200], available=available)
            return result

        except Exception:
            log.error("search_client.check.error", mpn=mpn[:200], exc_info=True)
            return {"available": False, "url": None, "formats": [], "confidence": "low", "mpn": mpn, "error": "search failed"}

    async def check_batch(self, mpns: list[str]) -> list[dict[str, Any]]:
        """Check availability for multiple MPNs sequentially."""
        log.info("search_client.check_batch", count=len(mpns))
        results: list[dict[str, Any]] = []

        for mpn in mpns:
            result = await self.check_availability(mpn)
            results.append(result)

        return results
