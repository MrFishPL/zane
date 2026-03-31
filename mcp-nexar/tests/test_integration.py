"""Integration tests for the mcp-nexar MCP server with mocked HTTP."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TOKEN_URL = "https://identity.nexar.com/connect/token"
GRAPHQL_URL = "https://api.nexar.com/graphql"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_routing_mock(token_response, graphql_data=None, graphql_status=200,
                       graphql_text=None, graphql_side_effect=None):
    """Create a single mock httpx.AsyncClient that routes by URL."""
    token_resp = httpx.Response(
        status_code=200,
        json=token_response,
        request=httpx.Request("POST", TOKEN_URL),
    )

    if graphql_data is not None:
        # Wrap in {"data": ...} envelope if not already wrapped
        if "data" not in graphql_data and "errors" not in graphql_data:
            graphql_data = {"data": graphql_data}
        gql_resp = httpx.Response(
            status_code=graphql_status,
            json=graphql_data,
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

    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Ensure env vars are set for all integration tests."""
    monkeypatch.setenv("NEXAR_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("NEXAR_CLIENT_SECRET", "test-client-secret")


class TestFullSearchFlow:
    """Test the full flow: server tool -> client -> auth -> response."""

    @pytest.mark.asyncio
    async def test_search_parts_tool_returns_compressed_results(self):
        """Test that search_parts tool returns properly compressed results."""
        from nexar_client import NexarClient

        client = NexarClient("test-client-id", "test-client-secret")
        token_data = _load_fixture("token_response.json")
        search_data = _load_fixture("search_response.json")

        mock_cls = _make_routing_mock(token_data, search_data["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.search_parts("3 ohm resistor 0603")

        # Verify structure
        assert "hits" in result
        assert "results" in result
        assert isinstance(result["results"], list)

        # Verify first part is complete
        part = result["results"][0]
        assert part["mpn"] == "RC0603FR-073RL"
        assert part["manufacturer"] == "Yageo"
        assert part["description"] is not None
        assert part["lifecycle"] == "active"
        assert part["datasheet_url"] is not None

        # Verify specs are filtered to key specs only
        spec_names = {s["name"] for s in part["specs"]}
        assert "Resistance" in spec_names
        assert "Tolerance" in spec_names
        # Non-key specs should not be present
        assert "Series" not in spec_names
        assert "Composition" not in spec_names

        # Verify sellers are compressed
        assert len(part["sellers"]) <= 5
        for seller in part["sellers"]:
            assert "name" in seller
            assert "offers" in seller
            for offer in seller["offers"]:
                assert len(offer["prices"]) <= 3

    @pytest.mark.asyncio
    async def test_multi_match_aggregates_results(self):
        """Test that multi_match correctly aggregates per-MPN results."""
        from nexar_client import NexarClient

        client = NexarClient("test-client-id", "test-client-secret")
        token_data = _load_fixture("token_response.json")
        search_data = _load_fixture("search_response.json")

        mock_cls = _make_routing_mock(token_data, search_data["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.multi_match(["RC0603FR-073RL", "CRCW06033R00FKEA"])

        assert "results" in result
        assert "errors" in result
        assert "RC0603FR-073RL" in result["results"]
        assert "CRCW06033R00FKEA" in result["results"]
        assert len(result["errors"]) == 0

    @pytest.mark.asyncio
    async def test_lifecycle_check_flow(self):
        """Test the full lifecycle check flow."""
        from nexar_client import NexarClient

        client = NexarClient("test-client-id", "test-client-secret")
        token_data = _load_fixture("token_response.json")
        obs_data = _load_fixture("lifecycle_obsolete_response.json")

        mock_cls = _make_routing_mock(token_data, obs_data["data"])

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.check_lifecycle("LM317T")

        assert result["lifecycle"] == "obsolete"
        assert result["mpn"] == "LM317T"
        assert result["manufacturer"] == "Texas Instruments"


class TestErrorRecovery:
    """Test that errors are handled gracefully through the full stack."""

    @pytest.mark.asyncio
    async def test_auth_failure_in_search(self):
        """Test that auth failures bubble up correctly."""
        from nexar_client import NexarClient

        client = NexarClient("bad-id", "bad-secret")

        error_resp = httpx.Response(
            status_code=401,
            text='{"error":"invalid_client"}',
            request=httpx.Request("POST", TOKEN_URL),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=error_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError, match="Nexar token request failed"):
                await client.search_parts("test")

    @pytest.mark.asyncio
    async def test_rate_limit_in_multi_match(self):
        """Test that rate limits in multi_match are captured per-MPN."""
        from nexar_client import NexarClient

        client = NexarClient("test-id", "test-secret")
        token_data = _load_fixture("token_response.json")

        mock_cls = _make_routing_mock(
            token_data,
            graphql_status=429,
            graphql_text="Rate limit exceeded",
        )

        with patch("httpx.AsyncClient", mock_cls):
            result = await client.multi_match(["MPN1", "MPN2"])

        # All MPNs should be in errors
        assert len(result["errors"]) == 2
        assert len(result["results"]) == 0
        assert "rate limit" in result["errors"]["MPN1"].lower()
