"""Unit tests for mcp-nexar tools with mocked HTTP responses."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from auth import NexarAuth
from nexar_client import NexarClient

TOKEN_URL = "https://identity.nexar.com/connect/token"
GRAPHQL_URL = "https://api.nexar.com/graphql"


def _make_graphql_response(data: dict) -> httpx.Response:
    """Create a mock httpx response for GraphQL requests."""
    return httpx.Response(
        status_code=200,
        json=data,
        request=httpx.Request("POST", GRAPHQL_URL),
    )


def _make_routing_mock(token_response, graphql_response=None, graphql_status=200,
                       graphql_text=None, graphql_side_effect=None):
    """Create a single mock httpx.AsyncClient that routes by URL.

    Since auth.py and nexar_client.py share the same httpx module object,
    we must use a single patch with URL-based dispatch.

    graphql_response should be the full response body as returned by the API,
    i.e. {"data": {"supSearch": ...}} -- wrap it if needed.
    """
    token_resp = httpx.Response(
        status_code=200,
        json=token_response,
        request=httpx.Request("POST", TOKEN_URL),
    )

    if graphql_response is not None:
        # Wrap in {"data": ...} envelope if not already wrapped
        if "data" not in graphql_response and "errors" not in graphql_response:
            graphql_response = {"data": graphql_response}
        gql_resp = httpx.Response(
            status_code=graphql_status,
            json=graphql_response,
            request=httpx.Request("POST", GRAPHQL_URL),
        )
    elif graphql_text is not None:
        gql_resp = httpx.Response(
            status_code=graphql_status,
            text=graphql_text,
            request=httpx.Request("POST", GRAPHQL_URL),
        )
    else:
        gql_resp = None

    async def _post(url, **kwargs):
        if "identity.nexar.com" in str(url):
            return token_resp
        if graphql_side_effect is not None:
            raise graphql_side_effect
        return gql_resp

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Use MagicMock for the class constructor (synchronous call),
    # returning mock_client which has async methods.
    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls


# ── Auth Tests ──


class TestNexarAuth:
    @pytest.mark.asyncio
    async def test_get_token_success(self, token_response):
        auth = NexarAuth("test-id", "test-secret")

        mock_response = httpx.Response(
            status_code=200,
            json=token_response,
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            token = await auth.get_token()

        assert token == token_response["access_token"]

    @pytest.mark.asyncio
    async def test_token_caching(self, token_response):
        auth = NexarAuth("test-id", "test-secret")

        mock_response = httpx.Response(
            status_code=200,
            json=token_response,
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            token1 = await auth.get_token()
            token2 = await auth.get_token()

        assert token1 == token2
        # Token should be fetched only once due to caching
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_token_failure(self):
        auth = NexarAuth("bad-id", "bad-secret")

        mock_response = httpx.Response(
            status_code=401,
            text="Unauthorized",
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="Nexar token request failed"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_get_headers(self, token_response):
        auth = NexarAuth("test-id", "test-secret")

        mock_response = httpx.Response(
            status_code=200,
            json=token_response,
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("auth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            headers = await auth.get_headers()

        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Content-Type"] == "application/json"


# ── NexarClient Tests ──


class TestSearchParts:
    @pytest.mark.asyncio
    async def test_search_parts_success(self, search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_parts("3 ohm resistor 0603")

        assert result["hits"] == 1234
        assert len(result["results"]) == 2
        assert result["results"][0]["mpn"] == "RC0603FR-073RL"
        assert result["results"][0]["manufacturer"] == "Yageo"
        # New fields present
        assert result["results"][0]["total_avail"] == 2270000
        assert result["results"][0]["category"] == "Resistors"
        assert result["results"][0]["octopart_url"] == "https://octopart.com/rc0603fr-073rl-yageo-123456"
        # Old unauthorized fields absent
        assert "specs" not in result["results"][0]
        assert "datasheet_url" not in result["results"][0]
        assert "lifecycle" not in result["results"][0]

    @pytest.mark.asyncio
    async def test_search_parts_empty(self, empty_search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, empty_search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_parts("nonexistent_part_xyz_999")

        assert result["hits"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_search_parts_has_moq_and_sku(self, search_response, token_response):
        """Verify moq and sku are included in compressed seller offers."""
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_parts("3 ohm resistor 0603")

        first_offer = result["results"][0]["sellers"][0]["offers"][0]
        assert "moq" in first_offer
        assert "sku" in first_offer
        assert first_offer["moq"] == 1
        assert first_offer["sku"] == "311-3.0HRCT-ND"


class TestSearchMPN:
    @pytest.mark.asyncio
    async def test_search_mpn_success(self, token_response):
        """search_mpn uses supSearchMpn root key."""
        client = NexarClient("test-id", "test-secret")
        mpn_response = {
            "data": {
                "supSearchMpn": {
                    "hits": 1,
                    "results": [
                        {
                            "part": {
                                "mpn": "RC0603FR-073RL",
                                "manufacturer": {"name": "Yageo"},
                                "shortDescription": "3 Ohms 0603 Resistor",
                                "totalAvail": 2270000,
                                "category": {"name": "Resistors"},
                                "octopartUrl": "https://octopart.com/rc0603fr-073rl-yageo-123456",
                                "medianPrice1000": {"price": 0.0023, "currency": "USD"},
                                "sellers": []
                            }
                        }
                    ]
                }
            }
        }
        mock_cls = _make_routing_mock(token_response, mpn_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_mpn("RC0603FR-073RL")

        assert result["hits"] == 1
        assert result["results"][0]["mpn"] == "RC0603FR-073RL"
        assert result["results"][0]["total_avail"] == 2270000
        assert result["results"][0]["octopart_url"] == "https://octopart.com/rc0603fr-073rl-yageo-123456"


class TestMultiMatch:
    @pytest.mark.asyncio
    async def test_multi_match_success(self, token_response):
        """multi_match uses native supMultiMatch query."""
        client = NexarClient("test-id", "test-secret")
        multi_response = {
            "data": {
                "supMultiMatch": [
                    {
                        "hits": 1,
                        "parts": [
                            {
                                "mpn": "RC0603FR-073RL",
                                "manufacturer": {"name": "Yageo"},
                                "shortDescription": "3 Ohms Resistor",
                                "totalAvail": 2270000,
                                "category": {"name": "Resistors"},
                                "octopartUrl": "https://octopart.com/rc0603fr-073rl-yageo-123456",
                                "medianPrice1000": {"price": 0.0023, "currency": "USD"},
                                "sellers": []
                            }
                        ]
                    },
                    {
                        "hits": 1,
                        "parts": [
                            {
                                "mpn": "CRCW06033R00FKEA",
                                "manufacturer": {"name": "Vishay Dale"},
                                "shortDescription": "3 Ohms Resistor",
                                "totalAvail": 200000,
                                "category": {"name": "Resistors"},
                                "octopartUrl": "https://octopart.com/crcw06033r00fkea-vishay-789012",
                                "medianPrice1000": {"price": 0.0035, "currency": "USD"},
                                "sellers": []
                            }
                        ]
                    }
                ]
            }
        }
        mock_cls = _make_routing_mock(token_response, multi_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.multi_match(["RC0603FR-073RL", "CRCW06033R00FKEA"])

        assert "results" in result
        assert "errors" in result
        assert len(result["results"]) == 2
        assert len(result["errors"]) == 0
        assert result["results"]["RC0603FR-073RL"]["results"][0]["mpn"] == "RC0603FR-073RL"
        assert result["results"]["CRCW06033R00FKEA"]["results"][0]["mpn"] == "CRCW06033R00FKEA"

    @pytest.mark.asyncio
    async def test_multi_match_empty(self, token_response):
        """multi_match handles empty results."""
        client = NexarClient("test-id", "test-secret")
        multi_response = {
            "data": {
                "supMultiMatch": [
                    {"hits": 0, "parts": []},
                ]
            }
        }
        mock_cls = _make_routing_mock(token_response, multi_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.multi_match(["NONEXISTENT123"])

        assert result["results"]["NONEXISTENT123"]["hits"] == 0
        assert result["results"]["NONEXISTENT123"]["results"] == []


# ── Error Handling Tests ──


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_rate_limiting(self, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(
            token_response,
            graphql_status=429,
            graphql_text="Rate limit exceeded",
        )

        with patch("httpx.AsyncClient", mock_cls):
            with pytest.raises(RuntimeError, match="rate limit"):
                await client.search_parts("test query")

    @pytest.mark.asyncio
    async def test_graphql_errors(self, token_response, graphql_error_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, graphql_error_response)

        with patch("httpx.AsyncClient", mock_cls):
            with pytest.raises(RuntimeError, match="GraphQL errors"):
                await client.search_parts("test query")

    @pytest.mark.asyncio
    async def test_timeout(self, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(
            token_response,
            graphql_side_effect=httpx.ReadTimeout("Connection timed out"),
        )

        with patch("httpx.AsyncClient", mock_cls):
            with pytest.raises(httpx.ReadTimeout):
                await client.search_parts("test query")

    @pytest.mark.asyncio
    async def test_auth_failure_propagates(self):
        client = NexarClient("bad-id", "bad-secret")

        mock_response = httpx.Response(
            status_code=401,
            text="Invalid client credentials",
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="Nexar token request failed"):
                await client.search_parts("test query")


# ── Compression Tests ──


class TestCompression:
    def test_compress_part_new_fields(self):
        """Verify new fields (total_avail, category, octopart_url) are present."""
        client = NexarClient("test-id", "test-secret")

        part = {
            "mpn": "TEST123",
            "manufacturer": {"name": "TestCo"},
            "shortDescription": "A test part",
            "totalAvail": 500000,
            "category": {"name": "Capacitors"},
            "octopartUrl": "https://octopart.com/test123",
            "medianPrice1000": {"price": 0.01, "currency": "USD"},
            "sellers": [],
        }

        compressed = client._compress_part(part)

        assert compressed["mpn"] == "TEST123"
        assert compressed["manufacturer"] == "TestCo"
        assert compressed["total_avail"] == 500000
        assert compressed["category"] == "Capacitors"
        assert compressed["octopart_url"] == "https://octopart.com/test123"
        # Old unauthorized fields must not be present
        assert "specs" not in compressed
        assert "datasheet_url" not in compressed
        assert "lifecycle" not in compressed

    def test_compress_part_limits_sellers(self):
        """Verify max 5 sellers and max 3 price breaks each."""
        client = NexarClient("test-id", "test-secret")

        sellers = []
        for i in range(8):
            sellers.append({
                "company": {"name": f"Seller{i}"},
                "offers": [
                    {
                        "inventoryLevel": 1000,
                        "moq": 1,
                        "sku": f"SKU-{i}",
                        "prices": [
                            {"quantity": 1, "price": 0.01, "currency": "USD"},
                            {"quantity": 10, "price": 0.008, "currency": "USD"},
                            {"quantity": 100, "price": 0.005, "currency": "USD"},
                            {"quantity": 1000, "price": 0.003, "currency": "USD"},
                            {"quantity": 5000, "price": 0.002, "currency": "USD"},
                        ],
                        "clickUrl": f"https://example.com/{i}",
                    }
                ],
            })

        part = {
            "mpn": "TEST456",
            "manufacturer": {"name": "TestCo"},
            "shortDescription": "A part with many sellers",
            "totalAvail": 8000,
            "category": {"name": "Resistors"},
            "octopartUrl": "https://octopart.com/test456",
            "medianPrice1000": None,
            "sellers": sellers,
        }

        compressed = client._compress_part(part)

        # Max 5 sellers
        assert len(compressed["sellers"]) == 5
        # Max 3 price breaks per offer
        for seller in compressed["sellers"]:
            for offer in seller["offers"]:
                assert len(offer["prices"]) <= 3

    def test_compress_part_empty(self):
        """Verify compress handles empty/None part."""
        client = NexarClient("test-id", "test-secret")
        assert client._compress_part({}) == {}
        assert client._compress_part(None) == {}

    def test_compress_part_missing_optional_fields(self):
        """Verify compress handles missing optional fields gracefully."""
        client = NexarClient("test-id", "test-secret")

        part = {
            "mpn": "MINIMAL",
            "manufacturer": None,
            "shortDescription": None,
            "totalAvail": None,
            "category": None,
            "octopartUrl": None,
            "medianPrice1000": None,
            "sellers": None,
        }

        compressed = client._compress_part(part)

        assert compressed["mpn"] == "MINIMAL"
        assert compressed["manufacturer"] is None
        assert compressed["total_avail"] is None
        assert compressed["category"] is None
        assert compressed["octopart_url"] is None
        assert compressed["sellers"] == []

    def test_compress_results_with_root_key(self):
        """Verify _compress_results respects the root_key parameter."""
        client = NexarClient("test-id", "test-secret")

        data = {
            "supSearchMpn": {
                "hits": 1,
                "results": [
                    {
                        "part": {
                            "mpn": "TEST-MPN",
                            "manufacturer": {"name": "TestCo"},
                            "shortDescription": "A part",
                            "totalAvail": 100,
                            "category": {"name": "ICs"},
                            "octopartUrl": "https://octopart.com/test-mpn",
                            "medianPrice1000": None,
                            "sellers": [],
                        }
                    }
                ]
            }
        }

        result = client._compress_results(data, root_key="supSearchMpn")
        assert result["hits"] == 1
        assert result["results"][0]["mpn"] == "TEST-MPN"

    def test_compress_part_includes_moq_and_sku(self):
        """Verify moq and sku are preserved in compressed offers."""
        client = NexarClient("test-id", "test-secret")

        part = {
            "mpn": "TEST789",
            "manufacturer": {"name": "TestCo"},
            "shortDescription": "A part",
            "totalAvail": 1000,
            "category": {"name": "Capacitors"},
            "octopartUrl": "https://octopart.com/test789",
            "medianPrice1000": None,
            "sellers": [
                {
                    "company": {"name": "DigiKey"},
                    "offers": [
                        {
                            "inventoryLevel": 5000,
                            "moq": 10,
                            "sku": "DK-TEST-789",
                            "prices": [{"quantity": 10, "price": 0.05, "currency": "USD"}],
                            "clickUrl": "https://digikey.com/test789",
                        }
                    ],
                }
            ],
        }

        compressed = client._compress_part(part)
        offer = compressed["sellers"][0]["offers"][0]
        assert offer["moq"] == 10
        assert offer["sku"] == "DK-TEST-789"


# ── Country/Currency Tests ──


class TestCountryCurrency:
    def test_default_country_currency(self):
        """Verify defaults are US/USD when no env vars set."""
        client = NexarClient("test-id", "test-secret")
        assert client._country == "US"
        assert client._currency == "USD"

    def test_explicit_country_currency(self):
        """Verify explicit params override defaults."""
        client = NexarClient("test-id", "test-secret", country="DE", currency="EUR")
        assert client._country == "DE"
        assert client._currency == "EUR"

    def test_env_country_currency(self, monkeypatch):
        """Verify env vars are used when no explicit params given."""
        monkeypatch.setenv("NEXAR_COUNTRY", "GB")
        monkeypatch.setenv("NEXAR_CURRENCY", "GBP")
        client = NexarClient("test-id", "test-secret")
        assert client._country == "GB"
        assert client._currency == "GBP"
