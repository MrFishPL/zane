"""Client for the TME (Transfer Multisort Elektronik) API.

Handles HMAC-SHA1 authentication and provides async methods for
product search, pricing, and stock lookup.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

API_BASE = "https://api.tme.eu"


def _sign_request(
    action: str,
    params: dict[str, str],
    app_secret: str,
) -> str:
    """Generate HMAC-SHA1 signature for a TME API request.

    Follows the OAuth 1.0a-style signing process:
    1. Sort parameters alphabetically
    2. URL-encode them
    3. Build base string: POST&url_encoded(api_url)&url_encoded(encoded_params)
    4. Sign with HMAC-SHA1 using app_secret
    5. Base64-encode the result
    """
    api_url = f"{API_BASE}/{action}.json"

    sorted_params = sorted(params.items())
    encoded_params = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)

    signature_base = (
        "POST"
        + "&"
        + urllib.parse.quote(api_url, safe="")
        + "&"
        + urllib.parse.quote(encoded_params, safe="")
    )

    sig = hmac.new(
        app_secret.encode("utf-8"),
        signature_base.encode("utf-8"),
        hashlib.sha1,
    ).digest()

    return base64.b64encode(sig).decode("utf-8").strip()


def _flatten_list_params(params: dict[str, Any]) -> dict[str, str]:
    """Flatten list parameters into TME's indexed format.

    TME expects: SymbolList[0]=X&SymbolList[1]=Y
    """
    flat: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, list):
            for i, item in enumerate(value):
                flat[f"{key}[{i}]"] = str(item)
        else:
            flat[key] = str(value)
    return flat


class TMEClient:
    """Async client for TME electronic component API."""

    def __init__(
        self,
        token: str | None = None,
        app_secret: str | None = None,
        language: str | None = None,
        country: str | None = None,
    ) -> None:
        self._token = token or os.environ.get("TME_APP_TOKEN", "")
        self._app_secret = app_secret or os.environ.get("TME_APP_SECRET", "")
        self._language = language or os.environ.get("TME_LANGUAGE", "EN")
        self._country = country or os.environ.get("TME_COUNTRY", "PL")
        self._http = httpx.AsyncClient(timeout=30.0)
        # Rate limiters (instance-scoped, safe across event loop restarts)
        self._price_lock = asyncio.Lock()
        self._last_price_call = 0.0
        self._general_lock = asyncio.Lock()
        self._last_general_call = 0.0

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _call(
        self,
        action: str,
        params: dict[str, Any],
        rate_limited: bool = False,
    ) -> dict[str, Any]:
        """Make a signed POST request to the TME API."""
        # Enforce rate limit for price/stock endpoints (2 req/s)
        if rate_limited:
            async with self._price_lock:
                elapsed = time.monotonic() - self._last_price_call
                if elapsed < 0.5:
                    await asyncio.sleep(0.5 - elapsed)
                self._last_price_call = time.monotonic()

        # Enforce general rate limit (10 req/s)
        async with self._general_lock:
            elapsed = time.monotonic() - self._last_general_call
            if elapsed < 0.1:
                await asyncio.sleep(0.1 - elapsed)
            self._last_general_call = time.monotonic()

        # Add auth params
        params["Token"] = self._token
        params["Language"] = self._language
        if self._country:
            params["Country"] = self._country

        # Flatten list params and sign
        flat_params = _flatten_list_params(params)
        signature = _sign_request(action, flat_params, self._app_secret)
        flat_params["ApiSignature"] = signature

        api_url = f"{API_BASE}/{action}.json"

        response = await self._http.post(
            api_url,
            data=flat_params,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "5")
            log.warning("tme_client.rate_limited", retry_after=retry_after)
            raise RuntimeError(f"TME API rate limit exceeded. Retry after {retry_after}s.")

        if response.status_code != 200:
            log.error(
                "tme_client.request_failed",
                action=action,
                status=response.status_code,
                body=response.text[:300],
            )
            raise RuntimeError(
                f"TME API request failed: {response.status_code} {response.text[:200]}"
            )

        data = response.json()
        if data.get("Status") != "OK":
            error = data.get("Status", "UNKNOWN_ERROR")
            log.error("tme_client.api_error", action=action, status=error)
            raise RuntimeError(f"TME API error: {error}")

        return data.get("Data", {})

    def _compress_product(
        self,
        product: dict[str, Any],
        pricing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compress a TME product into the format expected by the search agent.

        Matches the Nexar output format so the agent doesn't need changes.
        """
        symbol = product.get("Symbol", "")
        producer = product.get("Producer", "")
        description = product.get("Description", "")
        category = product.get("Category", "")
        photo = product.get("Photo", "")
        product_url = product.get("ProductInformationPage", "")

        # Build sellers/offers from pricing data
        sellers = []
        total_stock = 0
        unit_price = None
        currency = None

        if pricing:
            stock = pricing.get("Amount", 0)
            total_stock = stock
            price_list = pricing.get("PriceList", [])

            offers = []
            for tier in price_list[:5]:
                price_val = tier.get("PriceValue", 0)
                qty = tier.get("Amount", 1)
                offers.append({
                    "stock": stock,
                    "moq": qty,
                    "sku": symbol,
                    "prices": [{"quantity": qty, "price": price_val, "currency": "PLN"}],
                    "url": f"https://www.tme.eu/en/details/{symbol}/",
                })
                if unit_price is None:
                    unit_price = price_val

            currency = "PLN"
            sellers.append({
                "name": "TME",
                "offers": offers[:3],
            })

        return {
            "mpn": product.get("OriginalSymbol") or symbol,
            "manufacturer": producer,
            "description": description,
            "total_avail": total_stock,
            "category": category,
            "tme_url": f"https://www.tme.eu/en/details/{symbol}/" if symbol else None,
            "median_price_1000": None,
            "sellers": sellers,
            "unit_price": unit_price,
            "currency": currency,
            "photo": photo,
            "tme_symbol": symbol,
        }

    async def search_parts(self, query: str) -> dict[str, Any]:
        """Search for electronic components by description.

        Returns results in the same format as Nexar's search_parts
        so the agent search loop works unchanged.
        """
        start = time.monotonic()
        log.info("tme_client.search_parts", query=query[:200])

        try:
            # Step 1: Search for products
            search_data = await self._call("Products/Search", {
                "SearchPlain": query,
                "SearchWithStock": "true",
            })

            products = search_data.get("ProductList", [])[:5]
            total = search_data.get("Amount", 0)

            if not products:
                duration_ms = round((time.monotonic() - start) * 1000)
                log.info("tme_client.search_parts.empty", query=query, duration_ms=duration_ms)
                return {"hits": 0, "results": []}

            # Step 2: Get prices and stock for found symbols
            symbols = [p["Symbol"] for p in products]
            pricing_data = await self._call(
                "Products/GetPricesAndStocks",
                {"SymbolList": symbols, "Currency": "PLN"},
                rate_limited=True,
            )

            pricing_map = {
                p["Symbol"]: p for p in pricing_data.get("ProductList", [])
            }

            # Combine product info + pricing
            results = []
            for product in products:
                sym = product["Symbol"]
                results.append(self._compress_product(product, pricing_map.get(sym)))

            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "tme_client.search_parts.ok",
                hits=total,
                results=len(results),
                duration_ms=duration_ms,
            )
            return {"hits": total, "results": results}

        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.search_parts.error", duration_ms=duration_ms, exc_info=True)
            raise

    async def search_mpn(self, mpn: str) -> dict[str, Any]:
        """Search for a component by manufacturer part number / TME symbol."""
        start = time.monotonic()
        log.info("tme_client.search_mpn", mpn=mpn[:200])

        try:
            # Try exact symbol lookup first
            try:
                product_data = await self._call(
                    "Products/GetProducts",
                    {"SymbolList": [mpn]},
                )
                products = product_data.get("ProductList", [])
            except RuntimeError:
                products = []

            # If exact match fails, fall back to search
            if not products:
                search_data = await self._call("Products/Search", {
                    "SearchPlain": mpn,
                    "SearchWithStock": "true",
                })
                products = search_data.get("ProductList", [])[:3]

            if not products:
                duration_ms = round((time.monotonic() - start) * 1000)
                log.info("tme_client.search_mpn.empty", mpn=mpn, duration_ms=duration_ms)
                return {"hits": 0, "results": []}

            # Get pricing
            symbols = [p["Symbol"] for p in products]
            pricing_data = await self._call(
                "Products/GetPricesAndStocks",
                {"SymbolList": symbols, "Currency": "PLN"},
                rate_limited=True,
            )

            pricing_map = {
                p["Symbol"]: p for p in pricing_data.get("ProductList", [])
            }

            results = []
            for product in products:
                sym = product["Symbol"]
                results.append(self._compress_product(product, pricing_map.get(sym)))

            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "tme_client.search_mpn.ok",
                hits=len(results),
                results=len(results),
                duration_ms=duration_ms,
            )
            return {"hits": len(results), "results": results}

        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.search_mpn.error", duration_ms=duration_ms, exc_info=True)
            raise

    async def get_categories(self, category_id: int | None = None) -> dict[str, Any]:
        """Get TME category tree (or subtree starting from category_id)."""
        start = time.monotonic()
        log.info("tme_client.get_categories", category_id=category_id)

        params: dict[str, Any] = {"Tree": "true"}
        if category_id is not None:
            params["CategoryId"] = str(category_id)

        try:
            data = await self._call("Products/GetCategories", params)
            duration_ms = round((time.monotonic() - start) * 1000)
            log.info("tme_client.get_categories.ok", duration_ms=duration_ms)
            return data
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.get_categories.error", duration_ms=duration_ms, exc_info=True)
            raise

    async def get_parameters(self, symbols: list[str]) -> dict[str, Any]:
        """Get technical parameters for products (resistance, package, voltage, etc.)."""
        start = time.monotonic()
        log.info("tme_client.get_parameters", count=len(symbols))

        try:
            data = await self._call("Products/GetParameters", {"SymbolList": symbols[:50]})
            duration_ms = round((time.monotonic() - start) * 1000)
            log.info("tme_client.get_parameters.ok", duration_ms=duration_ms)
            return data
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.get_parameters.error", duration_ms=duration_ms, exc_info=True)
            raise

    async def get_similar_products(self, symbols: list[str]) -> dict[str, Any]:
        """Find similar/alternative products for given symbols."""
        start = time.monotonic()
        log.info("tme_client.get_similar_products", count=len(symbols))

        try:
            data = await self._call("Products/GetSimilarProducts", {"SymbolList": symbols[:50]})
            duration_ms = round((time.monotonic() - start) * 1000)
            log.info("tme_client.get_similar_products.ok", duration_ms=duration_ms)
            return data
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.get_similar_products.error", duration_ms=duration_ms, exc_info=True)
            raise

    async def search_parts_in_category(self, query: str, category_id: str) -> dict[str, Any]:
        """Search for components within a specific TME category."""
        start = time.monotonic()
        log.info("tme_client.search_parts_in_category", query=query[:200], category_id=category_id)

        try:
            search_data = await self._call("Products/Search", {
                "SearchPlain": query,
                "SearchCategory": category_id,
                "SearchWithStock": "true",
            })

            products = search_data.get("ProductList", [])[:5]
            total = search_data.get("Amount", 0)

            if not products:
                duration_ms = round((time.monotonic() - start) * 1000)
                log.info("tme_client.search_parts_in_category.empty", duration_ms=duration_ms)
                return {"hits": 0, "results": []}

            symbols = [p["Symbol"] for p in products]
            pricing_data = await self._call(
                "Products/GetPricesAndStocks",
                {"SymbolList": symbols, "Currency": "PLN"},
                rate_limited=True,
            )

            pricing_map = {p["Symbol"]: p for p in pricing_data.get("ProductList", [])}

            results = []
            for product in products:
                sym = product["Symbol"]
                results.append(self._compress_product(product, pricing_map.get(sym)))

            duration_ms = round((time.monotonic() - start) * 1000)
            log.info("tme_client.search_parts_in_category.ok", hits=total, results=len(results), duration_ms=duration_ms)
            return {"hits": total, "results": results}

        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.search_parts_in_category.error", duration_ms=duration_ms, exc_info=True)
            raise

    async def multi_match(self, mpns: list[str]) -> dict[str, Any]:
        """Batch lookup of multiple MPNs.

        TME supports up to 50 symbols per request.
        """
        start = time.monotonic()
        log.info("tme_client.multi_match", count=len(mpns))

        try:
            # Batch into chunks of 50 (TME limit)
            all_results: dict[str, Any] = {}
            errors: dict[str, str] = {}

            for i in range(0, len(mpns), 50):
                chunk = mpns[i : i + 50]

                try:
                    # Get product details
                    product_data = await self._call(
                        "Products/GetProducts",
                        {"SymbolList": chunk},
                    )
                    products = product_data.get("ProductList", [])

                    # Get pricing
                    if products:
                        symbols = [p["Symbol"] for p in products]
                        pricing_data = await self._call(
                            "Products/GetPricesAndStocks",
                            {"SymbolList": symbols, "Currency": "PLN"},
                            rate_limited=True,
                        )
                        pricing_map = {
                            p["Symbol"]: p
                            for p in pricing_data.get("ProductList", [])
                        }
                    else:
                        pricing_map = {}

                    product_map = {p["Symbol"]: p for p in products}

                    for mpn in chunk:
                        product = product_map.get(mpn)
                        if product:
                            compressed = self._compress_product(
                                product, pricing_map.get(mpn)
                            )
                            all_results[mpn] = {"hits": 1, "results": [compressed]}
                        else:
                            all_results[mpn] = {"hits": 0, "results": []}

                except Exception as exc:
                    for mpn in chunk:
                        errors[mpn] = str(exc)
                        all_results[mpn] = {"hits": 0, "results": []}

            duration_ms = round((time.monotonic() - start) * 1000)
            log.info(
                "tme_client.multi_match.ok",
                total=len(mpns),
                found=sum(1 for v in all_results.values() if v["hits"] > 0),
                duration_ms=duration_ms,
            )
            return {"results": all_results, "errors": errors}

        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000)
            log.error("tme_client.multi_match.error", duration_ms=duration_ms, exc_info=True)
            raise
