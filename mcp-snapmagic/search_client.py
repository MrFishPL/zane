"""SnapMagic/SnapEDA CAD availability checker using Tavily agentic search.

Uses advanced search with content extraction to verify that a SnapEDA
page exists for the exact MPN and determine available CAD formats.
"""

import asyncio
import os
import re
from typing import Any
from urllib.parse import unquote

import httpx
import structlog

log = structlog.get_logger()

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
REQUEST_TIMEOUT = 20.0

KNOWN_FORMATS = {"kicad", "altium", "eagle"}


def _mpn_matches(mpn: str, url: str, content: str) -> bool:
    """Check if the search result actually matches the requested MPN."""
    mpn_lower = mpn.lower()
    content_lower = content.lower()

    # Extract part name from SnapEDA URL path: /parts/BFP740/Infineon/...
    url_part = ""
    if "/parts/" in url:
        url_part = unquote(url.split("/parts/")[1].split("/")[0]).lower()

    # Exact MPN in content
    if mpn_lower in content_lower:
        return True

    # URL part is a prefix of or equal to the MPN (e.g. BFP740 matches BFP740H6327XTSA1)
    if url_part and mpn_lower.startswith(url_part):
        return True

    # Strip common ordering suffixes and check base
    base = re.sub(r'[-/]?(PU|AU|RL|CT|TR|ND|DKR|GI|XTSA\d?)$', '', mpn_lower, flags=re.IGNORECASE)
    if base and base in content_lower:
        return True

    return False


def _detect_formats(content: str) -> list[str]:
    """Detect available CAD formats from page content."""
    c = content.lower()
    formats = []
    if "kicad" in c:
        formats.append("kicad")
    if "altium" in c:
        formats.append("altium")
    if "eagle" in c:
        formats.append("eagle")
    # If page mentions downloads but no specific format, assume all
    if not formats and ("download" in c or "symbol" in c or "footprint" in c):
        formats = ["kicad", "altium", "eagle"]
    return sorted(set(formats))


class SnapMagicSearchClient:
    """Check SnapMagic/SnapEDA CAD model availability via Tavily search."""

    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        self._api_key = api_key or TAVILY_API_KEY
        self._timeout = timeout or REQUEST_TIMEOUT

    async def check_availability(self, mpn: str) -> dict[str, Any]:
        """Search SnapEDA for the MPN, verify match, and detect formats."""
        log.info("search_client.check", mpn=mpn[:200])

        if not self._api_key:
            log.warning("search_client.no_api_key")
            return {"available": False, "url": None, "formats": [], "confidence": "low", "mpn": mpn}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    TAVILY_SEARCH_URL,
                    json={
                        "api_key": self._api_key,
                        "query": f"site:snapeda.com \"{mpn}\"",
                        "search_depth": "advanced",
                        "max_results": 5,
                    },
                )
                resp.raise_for_status()
                search_results = resp.json().get("results", [])

            # Find a matching SnapEDA parts page
            for r in search_results:
                url = r.get("url", "")
                title = r.get("title", "")
                content = r.get("content", "")
                combined = f"{title} {content}"

                if "/parts/" not in url:
                    continue
                if "snapeda.com" not in url and "snapmagic.com" not in url:
                    continue

                if _mpn_matches(mpn, url, combined):
                    formats = _detect_formats(combined)
                    log.info("search_client.check.found", mpn=mpn[:200], url=url, formats=formats)
                    return {
                        "available": True,
                        "url": url,
                        "formats": formats,
                        "confidence": "high",
                        "mpn": mpn,
                    }

            log.info("search_client.check.not_found", mpn=mpn[:200])
            return {"available": False, "url": None, "formats": [], "confidence": "high", "mpn": mpn}

        except Exception:
            log.error("search_client.check.error", mpn=mpn[:200], exc_info=True)
            return {"available": False, "url": None, "formats": [], "confidence": "low", "mpn": mpn, "error": "search failed"}

    async def check_batch(self, mpns: list[str], format: str = "any") -> dict[str, Any]:
        """Check CAD availability for multiple MPNs concurrently."""
        log.info("search_client.check_batch", count=len(mpns))

        async def _check_one(mpn: str) -> tuple[str, dict]:
            result = await self.check_availability(mpn)
            return mpn, result

        tasks = [_check_one(mpn) for mpn in mpns]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, Any] = {}
        for item in results_list:
            if isinstance(item, Exception):
                continue
            mpn, result = item
            results[mpn] = result
        return results
