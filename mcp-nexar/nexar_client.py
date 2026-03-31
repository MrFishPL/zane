"""GraphQL client for the Nexar (Octopart) API."""

import time
from typing import Any

import httpx
import structlog

from auth import NexarAuth

log = structlog.get_logger()

GRAPHQL_URL = "https://api.nexar.com/graphql"

SEARCH_QUERY = """
query SearchParts($query: String!, $limit: Int!) {
  supSearch(q: $query, limit: $limit) {
    hits
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        medianPrice1000 { price currency }
        sellers {
          company { name }
          offers {
            inventoryLevel
            prices { quantity price currency }
            clickUrl
          }
        }
        v3uid
        counts
      }
    }
  }
}
"""

# Specs we always want to surface when available
KEY_SPECS = {
    "Resistance",
    "Capacitance",
    "Inductance",
    "Voltage Rating",
    "Voltage - Rated",
    "Power (Watts)",
    "Tolerance",
    "Package / Case",
    "Temperature Coefficient",
    "Operating Temperature",
    "Mounting Type",
    "Size / Dimension",
    "Lifecycle Status",
}


class NexarClient:
    """High-level client for Nexar component search."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._auth = NexarAuth(client_id, client_secret)

    async def _execute_query(
        self, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a GraphQL query against the Nexar API."""
        headers = await self._auth.get_headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                GRAPHQL_URL,
                headers=headers,
                json={"query": query, "variables": variables},
            )

        if response.status_code == 429:
            log.warning("nexar_client.rate_limited")
            raise RuntimeError("Nexar API rate limit exceeded. Please retry later.")

        if response.status_code != 200:
            log.error(
                "nexar_client.query_failed",
                status=response.status_code,
                body=response.text[:200],
            )
            raise RuntimeError(
                f"Nexar GraphQL query failed: {response.status_code} {response.text[:200]}"
            )

        data = response.json()
        if "errors" in data and "data" not in data:
            # Only raise if there's no usable data at all
            log.error("nexar_client.graphql_errors", errors=data["errors"])
            raise RuntimeError(f"Nexar GraphQL errors: {data['errors']}")
        if "errors" in data:
            # Partial errors (e.g. unauthorized fields) — log but continue with available data
            log.warning("nexar_client.partial_errors", count=len(data["errors"]))

        return data["data"]

    def _compress_part(self, part: dict[str, Any]) -> dict[str, Any]:
        """Compress a part result: key specs only, top 5 sellers, max 3 price breaks."""
        if not part:
            return {}

        # Filter specs to key ones
        specs = []
        if part.get("specs"):
            for spec in part["specs"]:
                attr_name = spec.get("attribute", {}).get("name", "")
                if attr_name in KEY_SPECS:
                    specs.append(
                        {"name": attr_name, "value": spec.get("displayValue", "")}
                    )

        # Compress sellers: top 5, max 3 price breaks each
        sellers = []
        for seller in (part.get("sellers") or [])[:5]:
            compressed_offers = []
            for offer in seller.get("offers") or []:
                compressed_offers.append(
                    {
                        "stock": offer.get("inventoryLevel"),
                        "prices": (offer.get("prices") or [])[:3],
                        "url": offer.get("clickUrl"),
                    }
                )
            sellers.append(
                {
                    "name": seller.get("company", {}).get("name", ""),
                    "offers": compressed_offers[:3],
                }
            )

        # Determine lifecycle from specs
        lifecycle = "unknown"
        if part.get("specs"):
            for spec in part["specs"]:
                if spec.get("attribute", {}).get("name", "").lower() in (
                    "lifecycle status",
                    "lifecycle",
                ):
                    raw = (spec.get("displayValue") or "").lower()
                    if "active" in raw:
                        lifecycle = "active"
                    elif "nrnd" in raw or "not recommended" in raw:
                        lifecycle = "nrnd"
                    elif "obsolete" in raw or "discontinued" in raw:
                        lifecycle = "obsolete"
                    else:
                        lifecycle = raw or "unknown"
                    break

        result: dict[str, Any] = {
            "mpn": part.get("mpn"),
            "manufacturer": (part.get("manufacturer") or {}).get("name"),
            "description": part.get("shortDescription"),
            "specs": specs,
            "datasheet_url": (part.get("bestDatasheet") or {}).get("url"),
            "median_price_1000": part.get("medianPrice1000"),
            "sellers": sellers,
            "lifecycle": lifecycle,
        }
        return result

    def _compress_results(self, data: dict[str, Any]) -> dict[str, Any]:
        """Compress supSearch results into a concise format."""
        sup_search = data.get("supSearch", {})
        results = sup_search.get("results") or []

        parts = []
        for result in results:
            part = result.get("part")
            if part:
                parts.append(self._compress_part(part))

        return {
            "hits": sup_search.get("hits", 0),
            "results": parts,
        }

    async def search_parts(self, query: str) -> dict[str, Any]:
        """Search for electronic components by description."""
        start = time.monotonic()
        log.info("nexar_client.search_parts", query=query[:200])

        try:
            data = await self._execute_query(
                SEARCH_QUERY, {"query": query, "limit": 5}
            )
            result = self._compress_results(data)
            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "nexar_client.search_parts.ok",
                hits=result["hits"],
                results=len(result["results"]),
                duration_ms=duration_ms,
            )
            return result
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("nexar_client.search_parts.error", duration_ms=duration_ms)
            raise

    async def search_mpn(self, mpn: str) -> dict[str, Any]:
        """Search for a component by exact MPN."""
        start = time.monotonic()
        log.info("nexar_client.search_mpn", mpn=mpn[:200])

        try:
            data = await self._execute_query(
                SEARCH_QUERY, {"query": mpn, "limit": 5}
            )
            result = self._compress_results(data)
            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "nexar_client.search_mpn.ok",
                hits=result["hits"],
                results=len(result["results"]),
                duration_ms=duration_ms,
            )
            return result
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("nexar_client.search_mpn.error", duration_ms=duration_ms)
            raise

    async def multi_match(self, mpns: list[str]) -> dict[str, Any]:
        """Batch lookup of multiple MPNs."""
        start = time.monotonic()
        log.info("nexar_client.multi_match", count=len(mpns))

        results: dict[str, Any] = {}
        errors: dict[str, str] = {}

        for mpn in mpns:
            try:
                result = await self.search_mpn(mpn)
                results[mpn] = result
            except Exception as exc:
                log.warning("nexar_client.multi_match.mpn_error", mpn=mpn[:200])
                errors[mpn] = str(exc)

        duration_ms = round((time.monotonic() - start) * 1000)
        log.info(
            "nexar_client.multi_match.ok",
            total=len(mpns),
            success=len(results),
            errors=len(errors),
            duration_ms=duration_ms,
        )
        return {"results": results, "errors": errors}

    async def check_lifecycle(self, mpn: str) -> dict[str, Any]:
        """Check lifecycle status of a component."""
        start = time.monotonic()
        log.info("nexar_client.check_lifecycle", mpn=mpn[:200])

        try:
            data = await self._execute_query(
                SEARCH_QUERY, {"query": mpn, "limit": 1}
            )
            compressed = self._compress_results(data)

            if compressed["results"]:
                part = compressed["results"][0]
                result = {
                    "mpn": part["mpn"],
                    "manufacturer": part["manufacturer"],
                    "lifecycle": part["lifecycle"],
                }
            else:
                result = {
                    "mpn": mpn,
                    "manufacturer": None,
                    "lifecycle": "unknown",
                }

            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "nexar_client.check_lifecycle.ok",
                lifecycle=result["lifecycle"],
                duration_ms=duration_ms,
            )
            return result
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("nexar_client.check_lifecycle.error", duration_ms=duration_ms)
            raise

    async def get_quota_status(self) -> dict[str, Any]:
        """Return placeholder quota status information.

        The Nexar API does not expose a dedicated quota endpoint.
        This returns a static placeholder so callers can check
        that the auth flow works and the server is responsive.
        """
        log.info("nexar_client.get_quota_status")

        # Verify credentials work by ensuring we can get a token
        try:
            await self._auth.get_token()
            auth_ok = True
        except Exception:
            auth_ok = False

        return {
            "status": "ok" if auth_ok else "auth_error",
            "auth_valid": auth_ok,
            "note": "Nexar does not expose a quota endpoint. Check your dashboard at nexar.com for usage details.",
        }
