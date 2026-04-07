"""GraphQL client for the Nexar (Octopart) API."""

import itertools
import os
import time
from typing import Any

import httpx
import structlog

from auth import NexarAuth

log = structlog.get_logger()

GRAPHQL_URL = "https://api.nexar.com/graphql"

SEARCH_QUERY = """
query SearchParts($query: String!, $limit: Int!, $country: String!, $currency: String!) {
  supSearch(q: $query, limit: $limit, country: $country, currency: $currency) {
    hits
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        totalAvail
        category { name }
        octopartUrl
        medianPrice1000 { price currency }
        sellers(authorizedOnly: true) {
          company { name }
          offers {
            inventoryLevel
            moq
            sku
            prices { quantity price currency }
            clickUrl
          }
        }
      }
    }
  }
}
"""

SEARCH_MPN_QUERY = """
query SearchMPN($query: String!, $limit: Int!, $country: String!, $currency: String!) {
  supSearchMpn(q: $query, limit: $limit, country: $country, currency: $currency) {
    hits
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        totalAvail
        category { name }
        octopartUrl
        medianPrice1000 { price currency }
        sellers(authorizedOnly: true) {
          company { name }
          offers {
            inventoryLevel
            moq
            sku
            prices { quantity price currency }
            clickUrl
          }
        }
      }
    }
  }
}
"""

MULTI_MATCH_QUERY = """
query MultiMatch($queries: [SupPartMatchQuery!]!, $country: String!, $currency: String!) {
  supMultiMatch(queries: $queries, country: $country, currency: $currency) {
    hits
    parts {
      mpn
      manufacturer { name }
      shortDescription
      totalAvail
      category { name }
      octopartUrl
      medianPrice1000 { price currency }
      sellers(authorizedOnly: true) {
        company { name }
        offers {
          inventoryLevel
          moq
          sku
          prices { quantity price currency }
          clickUrl
        }
      }
    }
  }
}
"""


class NexarClient:
    """High-level client for Nexar component search."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        country: str | None = None,
        currency: str | None = None,
    ) -> None:
        self._auth = NexarAuth(client_id, client_secret)
        self._country = country or os.environ.get("NEXAR_COUNTRY", "US")
        self._currency = currency or os.environ.get("NEXAR_CURRENCY", "USD")

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
            # Partial errors (e.g. unauthorized fields) -- log but continue with available data
            log.warning("nexar_client.partial_errors", count=len(data["errors"]))

        return data["data"]

    def _compress_part(self, part: dict[str, Any]) -> dict[str, Any]:
        """Compress a part result: top 5 sellers, max 3 price breaks."""
        if not part:
            return {}

        # Compress sellers: top 5, max 3 price breaks each
        sellers = []
        for seller in (part.get("sellers") or [])[:5]:
            compressed_offers = []
            for offer in seller.get("offers") or []:
                compressed_offers.append(
                    {
                        "stock": offer.get("inventoryLevel"),
                        "moq": offer.get("moq"),
                        "sku": offer.get("sku"),
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

        result: dict[str, Any] = {
            "mpn": part.get("mpn"),
            "manufacturer": (part.get("manufacturer") or {}).get("name"),
            "description": part.get("shortDescription"),
            "total_avail": part.get("totalAvail"),
            "category": (part.get("category") or {}).get("name"),
            "octopart_url": part.get("octopartUrl"),
            "median_price_1000": part.get("medianPrice1000"),
            "sellers": sellers,
        }
        return result

    def _compress_results(
        self, data: dict[str, Any], root_key: str = "supSearch"
    ) -> dict[str, Any]:
        """Compress search results into a concise format."""
        sup_search = data.get(root_key, {})
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
                SEARCH_QUERY,
                {
                    "query": query,
                    "limit": 5,
                    "country": self._country,
                    "currency": self._currency,
                },
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
                SEARCH_MPN_QUERY,
                {
                    "query": mpn,
                    "limit": 3,
                    "country": self._country,
                    "currency": self._currency,
                },
            )
            result = self._compress_results(data, root_key="supSearchMpn")
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
        """Batch lookup of multiple MPNs using native supMultiMatch."""
        start = time.monotonic()
        log.info("nexar_client.multi_match", count=len(mpns))

        try:
            queries = [{"mpn": mpn, "limit": 3} for mpn in mpns]
            data = await self._execute_query(
                MULTI_MATCH_QUERY,
                {
                    "queries": queries,
                    "country": self._country,
                    "currency": self._currency,
                },
            )

            multi_results = data.get("supMultiMatch") or []
            results: dict[str, Any] = {}
            if len(multi_results) != len(mpns):
                log.warning(
                    "nexar_client.multi_match.length_mismatch",
                    expected=len(mpns),
                    got=len(multi_results),
                )
            for mpn, match in itertools.zip_longest(mpns, multi_results, fillvalue={}):
                if match is None:
                    results[mpn] = {"hits": 0, "results": []}
                    continue
                parts_raw = match.get("parts") or []
                parts = [self._compress_part(p) for p in parts_raw if p]
                results[mpn] = {
                    "hits": match.get("hits", len(parts)),
                    "results": parts,
                }

            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "nexar_client.multi_match.ok",
                total=len(mpns),
                success=len(results),
                duration_ms=duration_ms,
            )
            return {"results": results, "errors": {}}
        except Exception as exc:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("nexar_client.multi_match.error", duration_ms=duration_ms)
            raise
