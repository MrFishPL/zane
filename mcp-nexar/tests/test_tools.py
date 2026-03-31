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
        assert result["results"][0]["lifecycle"] == "active"

    @pytest.mark.asyncio
    async def test_search_parts_empty(self, empty_search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, empty_search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_parts("nonexistent_part_xyz_999")

        assert result["hits"] == 0
        assert result["results"] == []


class TestSearchMPN:
    @pytest.mark.asyncio
    async def test_search_mpn_success(self, search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_mpn("RC0603FR-073RL")

        assert result["hits"] == 1234
        assert result["results"][0]["mpn"] == "RC0603FR-073RL"


class TestMultiMatch:
    @pytest.mark.asyncio
    async def test_multi_match_success(self, search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.multi_match(["RC0603FR-073RL", "CRCW06033R00FKEA"])

        assert "results" in result
        assert "errors" in result
        assert len(result["results"]) == 2
        assert len(result["errors"]) == 0


class TestCheckLifecycle:
    @pytest.mark.asyncio
    async def test_lifecycle_active(self, search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.check_lifecycle("RC0603FR-073RL")

        assert result["lifecycle"] == "active"
        assert result["mpn"] == "RC0603FR-073RL"

    @pytest.mark.asyncio
    async def test_lifecycle_obsolete(self, token_response):
        client = NexarClient("test-id", "test-secret")

        with open("tests/fixtures/lifecycle_obsolete_response.json") as f:
            obsolete_data = json.load(f)

        mock_cls = _make_routing_mock(token_response, obsolete_data["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.check_lifecycle("LM317T")

        assert result["lifecycle"] == "obsolete"

    @pytest.mark.asyncio
    async def test_lifecycle_unknown(self, empty_search_response, token_response):
        client = NexarClient("test-id", "test-secret")
        mock_cls = _make_routing_mock(token_response, empty_search_response["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.check_lifecycle("NONEXISTENT123")

        assert result["lifecycle"] == "unknown"


class TestGetQuotaStatus:
    @pytest.mark.asyncio
    async def test_quota_status_ok(self, token_response):
        client = NexarClient("test-id", "test-secret")

        mock_response = httpx.Response(
            status_code=200,
            json=token_response,
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await client.get_quota_status()

        assert result["status"] == "ok"
        assert result["auth_valid"] is True

    @pytest.mark.asyncio
    async def test_quota_status_auth_error(self):
        client = NexarClient("bad-id", "bad-secret")

        mock_response = httpx.Response(
            status_code=401,
            text="Unauthorized",
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await client.get_quota_status()

        assert result["status"] == "auth_error"
        assert result["auth_valid"] is False


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
    def test_compress_part_filters_specs(self):
        """Verify only key specs are included in compressed output."""
        client = NexarClient("test-id", "test-secret")

        part = {
            "mpn": "TEST123",
            "manufacturer": {"name": "TestCo"},
            "shortDescription": "A test part",
            "specs": [
                {"attribute": {"name": "Resistance"}, "displayValue": "10 Ohms"},
                {"attribute": {"name": "Series"}, "displayValue": "X"},
                {"attribute": {"name": "Tolerance"}, "displayValue": "5%"},
                {"attribute": {"name": "Composition"}, "displayValue": "Thick Film"},
            ],
            "bestDatasheet": {"url": "https://example.com/ds.pdf"},
            "medianPrice1000": {"price": 0.01, "currency": "USD"},
            "sellers": [],
        }

        compressed = client._compress_part(part)

        spec_names = [s["name"] for s in compressed["specs"]]
        assert "Resistance" in spec_names
        assert "Tolerance" in spec_names
        assert "Series" not in spec_names
        assert "Composition" not in spec_names

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
            "specs": [],
            "bestDatasheet": None,
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
