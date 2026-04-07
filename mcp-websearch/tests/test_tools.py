"""Unit tests for mcp-websearch tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path so we can import search_client
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import search_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    """Load a JSON fixture file."""
    with open(FIXTURES / name) as f:
        return json.load(f)


def _mock_anthropic_response(fixture_name: str) -> MagicMock:
    """Create a mock Anthropic Message response from a fixture file."""
    data = _load_fixture(fixture_name)
    # Fixtures have OpenAI format: {"choices": [{"message": {"content": "..."}}]}
    # Extract the content string
    content_str = data["choices"][0]["message"]["content"]

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = content_str

    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    return response


# ---------------------------------------------------------------------------
# search_distributor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_mouser():
    """Test searching Mouser for an STM32 part."""
    mock_response = _mock_anthropic_response("search_mouser_stm32.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
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
    mock_response = _mock_anthropic_response("search_digikey_capacitor.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("100nF 0402 capacitor", "digikey.com")

    assert len(result["results"]) == 2
    for item in result["results"]:
        assert item["mpn_confidence"] == "searched"
        assert item["manufacturer"] is not None


@pytest.mark.asyncio
async def test_search_lcsc():
    """Test searching LCSC for resistors."""
    mock_response = _mock_anthropic_response("search_lcsc_resistor.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("100 ohm 0402 resistor", "lcsc.com")

    assert len(result["results"]) == 1
    assert result["results"][0]["mpn"] == "0402WGF1000TCE"
    assert result["results"][0]["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_tme():
    """Test searching TME for connectors."""
    mock_response = _mock_anthropic_response("search_tme_connector.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("JST XH 2-pin connector", "tme.eu")

    assert len(result["results"]) == 1
    assert result["results"][0]["manufacturer"] == "JST"
    assert result["results"][0]["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_farnell():
    """Test searching Farnell for inductors."""
    mock_response = _mock_anthropic_response("search_farnell_inductor.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
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
    mock_response = _mock_anthropic_response("fetch_product_page.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
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
    import anthropic as anthropic_mod

    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(
            side_effect=anthropic_mod.APITimeoutError(request=MagicMock())
        )
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert result["results"] == []
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_fetch_timeout():
    """Test that fetch_product_page handles timeout gracefully."""
    import anthropic as anthropic_mod

    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(
            side_effect=anthropic_mod.APITimeoutError(request=MagicMock())
        )
        MockClient.return_value = instance

        result = await search_client.fetch_product_page("https://www.mouser.com/example")

    assert "error" in result
    assert "timed out" in result["error"]
    assert result["mpn_confidence"] == "searched"


@pytest.mark.asyncio
async def test_search_empty_results():
    """Test handling of empty search results."""
    mock_response = _mock_anthropic_response("search_empty.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("nonexistent_part_xyz", "mouser.com")

    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_invalid_json():
    """Test handling of invalid JSON in LLM response."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "This is not valid JSON at all"

    bad_response = MagicMock()
    bad_response.content = [text_block]
    bad_response.stop_reason = "end_turn"

    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=bad_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert result["results"] == []
    assert "error" in result


@pytest.mark.asyncio
async def test_fetch_invalid_json():
    """Test handling of invalid JSON in fetch_product_page response."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I cannot access that URL"

    bad_response = MagicMock()
    bad_response.content = [text_block]
    bad_response.stop_reason = "end_turn"

    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=bad_response)
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
    mock_response = _mock_anthropic_response("search_digikey_capacitor.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("100nF capacitor", "digikey.com")

    for item in result["results"]:
        assert item["mpn_confidence"] == "searched", (
            f"Expected mpn_confidence='searched', got '{item.get('mpn_confidence')}'"
        )


@pytest.mark.asyncio
async def test_search_result_structure():
    """Verify the structure of search results."""
    mock_response = _mock_anthropic_response("search_mouser_stm32.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        result = await search_client.search_distributor("STM32F103C8T6", "mouser.com")

    assert "results" in result
    item = result["results"][0]
    expected_keys = {"mpn", "manufacturer", "description", "price", "stock", "url", "mpn_confidence"}
    assert expected_keys.issubset(set(item.keys()))


@pytest.mark.asyncio
async def test_web_search_fallback():
    """Test that if web_search tool is rejected (400), falls back to no-tool call."""
    import anthropic as anthropic_mod

    success_response = _mock_anthropic_response("search_mouser_stm32.json")

    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        # First call raises BadRequestError, second succeeds
        instance.messages.create = AsyncMock(
            side_effect=[
                anthropic_mod.BadRequestError(
                    message="Bad request",
                    response=MagicMock(status_code=400),
                    body={"error": {"message": "tool not supported"}},
                ),
                success_response,
            ]
        )
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


# ---------------------------------------------------------------------------
# Anthropic API integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uses_anthropic_api():
    """Verify that requests use the Anthropic SDK."""
    mock_response = _mock_anthropic_response("search_mouser_stm32.json")
    with patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        await search_client.search_distributor("STM32F103C8T6", "mouser.com")

        instance.messages.create.assert_called_once()
        call_kwargs = instance.messages.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["type"] == "web_search_20250305"


@pytest.mark.asyncio
async def test_uses_anthropic_api_key():
    """Verify that ANTHROPIC_API_KEY is used."""
    test_api_key = "sk-ant-test-12345"
    mock_response = _mock_anthropic_response("search_mouser_stm32.json")
    with patch("search_client.ANTHROPIC_API_KEY", test_api_key), \
         patch("search_client.anthropic.AsyncAnthropic") as MockClient:
        instance = AsyncMock()
        instance.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = instance

        await search_client.search_distributor("STM32F103C8T6", "mouser.com")

        MockClient.assert_called_once()
        call_kwargs = MockClient.call_args
        assert call_kwargs.kwargs.get("api_key") == test_api_key


def test_no_openai_references():
    """Verify that search_client no longer references OpenAI."""
    assert not hasattr(search_client, "OPENAI_API_KEY")
    assert not hasattr(search_client, "OPENAI_BASE_URL")
