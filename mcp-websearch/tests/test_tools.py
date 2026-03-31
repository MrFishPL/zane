"""Unit tests for mcp-websearch tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Add parent directory to path so we can import search_client
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import search_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture file."""
    with open(FIXTURES / name) as f:
        return json.load(f)


def _mock_response(fixture_name: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response from a fixture file."""
    data = _load_fixture(fixture_name)
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("POST", "http://test/v1/chat/completions"),
    )


# ---------------------------------------------------------------------------
# search_distributor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_mouser():
    """Test searching Mouser for an STM32 part."""
    mock_resp = _mock_response("search_mouser_stm32.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert "results" in result
    assert len(result["results"]) == 1
    assert result["results"][0]["mpn"] == "STM32F103C8T6"
    assert result["results"][0]["manufacturer"] == "STMicroelectronics"
    assert result["results"][0]["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_digikey():
    """Test searching DigiKey for capacitors."""
    mock_resp = _mock_response("search_digikey_capacitor.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("100nF 0402 capacitor", "digikey.com")

    assert len(result["results"]) == 2
    for item in result["results"]:
        assert item["mpn_confidence"] == "searched"
        assert item["manufacturer"] is not None


@pytest.mark.asyncio
async def test_search_lcsc():
    """Test searching LCSC for resistors."""
    mock_resp = _mock_response("search_lcsc_resistor.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("100 ohm 0402 resistor", "lcsc.com")

    assert len(result["results"]) == 1
    assert result["results"][0]["mpn"] == "0402WGF1000TCE"
    assert result["results"][0]["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_tme():
    """Test searching TME for connectors."""
    mock_resp = _mock_response("search_tme_connector.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("JST XH 2-pin connector", "tme.eu")

    assert len(result["results"]) == 1
    assert result["results"][0]["manufacturer"] == "JST"
    assert result["results"][0]["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_farnell():
    """Test searching Farnell for inductors."""
    mock_resp = _mock_response("search_farnell_inductor.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("10uH 1210 inductor", "farnell.com")

    assert len(result["results"]) == 1
    assert result["results"][0]["mpn"] == "LQH32CN100K53L"
    assert result["results"][0]["mpn_confidence"] == "searched"


# ---------------------------------------------------------------------------
# fetch_product_page tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_product_page():
    """Test fetching and parsing a product page."""
    mock_resp = _mock_response("fetch_product_page.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.fetch_product_page(
            "https://www.mouser.com/ProductDetail/STMicroelectronics/STM32F103C8T6"
        )

    assert result["mpn"] == "STM32F103C8T6"
    assert result["manufacturer"] == "STMicroelectronics"
    assert result["mpn_confidence"] == "searched"
    assert "specs" in result
    assert result["specs"]["core"] == "ARM Cortex-M3"


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_timeout():
    """Test that LLM timeout is handled gracefully."""
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.side_effect = httpx.TimeoutException("Request timed out")
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert result["results"] == []
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_fetch_timeout():
    """Test that fetch_product_page handles timeout gracefully."""
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.side_effect = httpx.TimeoutException("Request timed out")
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.fetch_product_page("https://www.mouser.com/example")

    assert "error" in result
    assert "timed out" in result["error"]
    assert result["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_empty_results():
    """Test handling of empty search results."""
    mock_resp = _mock_response("search_empty.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("nonexistent_part_xyz", "mouser.com")

    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_invalid_json():
    """Test handling of invalid JSON in LLM response."""
    bad_response = httpx.Response(
        status_code=200,
        json={
            "choices": [
                {
                    "message": {
                        "content": "This is not valid JSON at all"
                    }
                }
            ]
        },
        request=httpx.Request("POST", "http://test/v1/chat/completions"),
    )
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = bad_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert result["results"] == []
    assert "error" in result


@pytest.mark.asyncio
async def test_fetch_invalid_json():
    """Test handling of invalid JSON in fetch_product_page response."""
    bad_response = httpx.Response(
        status_code=200,
        json={
            "choices": [
                {
                    "message": {
                        "content": "I cannot access that URL"
                    }
                }
            ]
        },
        request=httpx.Request("POST", "http://test/v1/chat/completions"),
    )
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = bad_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.fetch_product_page("https://www.mouser.com/example")

    assert "error" in result
    assert result["mpn_confidence"] == "searched"


# ---------------------------------------------------------------------------
# Response format tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mpn_confidence_always_searched():
    """Verify that mpn_confidence is always 'searched' in all results."""
    mock_resp = _mock_response("search_digikey_capacitor.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("100nF capacitor", "digikey.com")

    for item in result["results"]:
        assert item["mpn_confidence"] == "searched", (
            f"Expected mpn_confidence='searched', got '{item.get('mpn_confidence')}'"
        )


@pytest.mark.asyncio
async def test_search_result_structure():
    """Verify the structure of search results."""
    mock_resp = _mock_response("search_mouser_stm32.json")
    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_resp
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert "results" in result
    item = result["results"][0]
    expected_keys = {"mpn", "manufacturer", "description", "price", "stock", "url", "mpn_confidence"}
    assert expected_keys.issubset(set(item.keys()))


@pytest.mark.asyncio
async def test_web_search_fallback():
    """Test that if web_search tool is rejected (400), falls back to no-tool call."""
    # First call raises 400 (web_search not supported), second call succeeds
    error_response = httpx.Response(
        status_code=400,
        json={"error": "web_search tool not supported"},
        request=httpx.Request("POST", "http://test/v1/chat/completions"),
    )
    success_response = _mock_response("search_mouser_stm32.json")

    with patch("search_client.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.side_effect = [
            httpx.HTTPStatusError(
                "Bad Request",
                request=error_response.request,
                response=error_response,
            ),
            success_response,
        ]
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert len(result["results"]) == 1
    assert result["results"][0]["mpn_confidence"] == "searched"


# ---------------------------------------------------------------------------
# _parse_json_response tests
# ---------------------------------------------------------------------------


def test_parse_json_plain():
    """Test parsing plain JSON."""
    text = '{"results": []}'
    assert search_client._parse_json_response(text) == {"results": []}


def test_parse_json_with_markdown_fences():
    """Test parsing JSON wrapped in markdown code fences."""
    text = '```json\n{"results": []}\n```'
    assert search_client._parse_json_response(text) == {"results": []}


def test_parse_json_with_whitespace():
    """Test parsing JSON with leading/trailing whitespace."""
    text = '  \n  {"results": []}  \n  '
    assert search_client._parse_json_response(text) == {"results": []}
