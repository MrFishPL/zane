"""Tests for TME client and MCP tools."""

import json
from unittest.mock import MagicMock

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tme_client import TMEClient, _sign_request, _flatten_list_params


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------

class TestSignature:
    def test_sign_request_deterministic(self):
        sig = _sign_request(
            "Products/Search",
            {"Token": "abc", "SearchPlain": "resistor", "Language": "EN"},
            "secret123",
        )
        assert isinstance(sig, str)
        assert len(sig) > 10  # base64-encoded HMAC

    def test_sign_request_changes_with_secret(self):
        params = {"Token": "abc", "SearchPlain": "resistor"}
        sig1 = _sign_request("Products/Search", params, "secret1")
        sig2 = _sign_request("Products/Search", params, "secret2")
        assert sig1 != sig2

    def test_sign_request_changes_with_action(self):
        params = {"Token": "abc", "SearchPlain": "resistor"}
        sig1 = _sign_request("Products/Search", params, "secret")
        sig2 = _sign_request("Products/GetProducts", params, "secret")
        assert sig1 != sig2


# ---------------------------------------------------------------------------
# Param flattening tests
# ---------------------------------------------------------------------------

class TestFlattenParams:
    def test_simple_params(self):
        result = _flatten_list_params({"Token": "abc", "Language": "EN"})
        assert result == {"Token": "abc", "Language": "EN"}

    def test_list_params(self):
        result = _flatten_list_params({"SymbolList": ["A", "B", "C"]})
        assert result == {
            "SymbolList[0]": "A",
            "SymbolList[1]": "B",
            "SymbolList[2]": "C",
        }

    def test_mixed_params(self):
        result = _flatten_list_params({
            "Token": "abc",
            "SymbolList": ["X"],
            "Language": "EN",
        })
        assert result == {
            "Token": "abc",
            "SymbolList[0]": "X",
            "Language": "EN",
        }


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = json.dumps(json_data or {})
    resp.headers = {}
    return resp


class TestTMEClient:
    @pytest.mark.asyncio
    async def test_search_parts_success(self, search_response, prices_response, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        call_count = 0
        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "Search" in url:
                return _mock_response(200, search_response)
            else:
                return _mock_response(200, prices_response)

        client._http.post = mock_post
        result = await client.search_parts("10k resistor 0603")

        assert result["hits"] == 245
        assert len(result["results"]) == 2
        assert result["results"][0]["mpn"] == "RC0603FR-0710KL"
        assert result["results"][0]["manufacturer"] == "YAGEO"
        assert result["results"][0]["total_avail"] == 5000000
        assert result["results"][0]["unit_price"] == 0.04

    @pytest.mark.asyncio
    async def test_search_parts_empty(self, empty_search_response, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        async def mock_post(url, **kwargs):
            return _mock_response(200, empty_search_response)

        client._http.post = mock_post
        result = await client.search_parts("nonexistent part xyz")

        assert result["hits"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_search_mpn_success(self, search_response, prices_response, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        async def mock_post(url, **kwargs):
            if "GetProducts" in url:
                return _mock_response(200, search_response)
            else:
                return _mock_response(200, prices_response)

        client._http.post = mock_post
        result = await client.search_mpn("RC0603FR-0710KL")

        assert result["hits"] > 0
        assert len(result["results"]) > 0

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        async def mock_post(url, **kwargs):
            resp = _mock_response(429)
            resp.headers = {"Retry-After": "5"}
            return resp

        client._http.post = mock_post
        with pytest.raises(RuntimeError, match="rate limit"):
            await client.search_parts("resistor")

    @pytest.mark.asyncio
    async def test_api_error_status(self, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        error_response = {"Status": "E_AUTHENTICATION_FAILED"}

        async def mock_post(url, **kwargs):
            return _mock_response(200, error_response)

        client._http.post = mock_post
        with pytest.raises(RuntimeError, match="E_AUTHENTICATION_FAILED"):
            await client.search_parts("resistor")


class TestCompression:
    def test_compress_product_with_pricing(self, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        product = {
            "Symbol": "RC0603FR-0710KL",
            "OriginalSymbol": "RC0603FR-0710KL",
            "Producer": "YAGEO",
            "Description": "Resistor 10k 0603",
            "Category": "SMD Resistors",
            "Photo": "https://example.com/photo.jpg",
            "ProductInformationPage": "https://www.tme.eu/en/details/rc0603fr-0710kl/",
        }

        pricing = {
            "Symbol": "RC0603FR-0710KL",
            "Amount": 5000000,
            "PriceList": [
                {"Amount": 10, "PriceValue": 0.04, "PriceBase": 0.04, "Special": False},
                {"Amount": 100, "PriceValue": 0.02, "PriceBase": 0.04, "Special": False},
            ],
        }

        result = client._compress_product(product, pricing)

        assert result["mpn"] == "RC0603FR-0710KL"
        assert result["manufacturer"] == "YAGEO"
        assert result["total_avail"] == 5000000
        assert result["unit_price"] == 0.04
        assert result["currency"] == "PLN"
        assert len(result["sellers"]) == 1
        assert result["sellers"][0]["name"] == "TME"

    def test_compress_product_without_pricing(self, env_vars):
        client = TMEClient(token="test", app_secret="secret")

        product = {
            "Symbol": "TEST123",
            "OriginalSymbol": "TEST123",
            "Producer": "TestCo",
            "Description": "Test part",
            "Category": "Test",
        }

        result = client._compress_product(product, None)

        assert result["mpn"] == "TEST123"
        assert result["total_avail"] == 0
        assert result["unit_price"] is None
        assert result["sellers"] == []
