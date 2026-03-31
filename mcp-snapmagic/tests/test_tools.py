"""Unit tests for mcp-snapmagic tools with mocked HTTP responses."""

import json
import pathlib
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Add parent dir to path so imports resolve when running from repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from search_client import (
    SnapMagicSearchClient,
    _extract_json,
    _normalise_result,
)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Helper: build a fake httpx.Response
# ---------------------------------------------------------------------------

def _mock_response(fixture_name: str, status: int = 200) -> httpx.Response:
    data = _load_fixture(fixture_name)
    return httpx.Response(
        status_code=status,
        json=data,
        request=httpx.Request("POST", "http://fake/v1/chat/completions"),
    )


# ---------------------------------------------------------------------------
# Unit tests for _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_extracts_from_code_block(self):
        text = 'Here is the result:\n```json\n{"available": true, "url": "https://x.com", "formats": ["KiCad"]}\n```'
        result = _extract_json(text)
        assert result is not None
        assert result["available"] is True

    def test_extracts_raw_json(self):
        text = '{"available": false, "url": null, "formats": []}'
        result = _extract_json(text)
        assert result is not None
        assert result["available"] is False

    def test_returns_none_for_no_json(self):
        text = "I cannot find this component."
        result = _extract_json(text)
        assert result is None

    def test_handles_malformed_json(self):
        text = '{"available": true, "url": }'
        result = _extract_json(text)
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests for _normalise_result
# ---------------------------------------------------------------------------

class TestNormaliseResult:
    def test_normalises_valid_result(self):
        raw = {"available": True, "url": "https://snapeda.com/part", "formats": ["KiCad", "Altium"]}
        result = _normalise_result(raw)
        assert result["available"] is True
        assert result["url"] == "https://snapeda.com/part"
        assert result["formats"] == ["kicad", "altium"]

    def test_normalises_none(self):
        result = _normalise_result(None)
        assert result["available"] is False
        assert result["url"] is None
        assert result["formats"] == []

    def test_filters_unknown_formats(self):
        raw = {"available": True, "url": "https://x.com", "formats": ["KiCad", "PADS", "OrCAD"]}
        result = _normalise_result(raw)
        assert result["formats"] == ["kicad"]

    def test_handles_string_formats(self):
        raw = {"available": True, "url": "https://x.com", "formats": "KiCad"}
        result = _normalise_result(raw)
        assert result["formats"] == ["kicad"]


# ---------------------------------------------------------------------------
# Integration tests for SnapMagicSearchClient
# ---------------------------------------------------------------------------

class TestCheckAvailability:
    @pytest.mark.asyncio
    async def test_available_component(self):
        """Component found on SnapMagic with all formats."""
        mock_resp = _mock_response("llm_available.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("STM32F103C8T6")

        assert result["available"] is True
        assert "snapeda.com" in result["url"]
        assert "kicad" in result["formats"]
        assert "altium" in result["formats"]
        assert "eagle" in result["formats"]
        assert result["mpn"] == "STM32F103C8T6"
        assert result["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_unavailable_component(self):
        """Component not found on SnapMagic."""
        mock_resp = _mock_response("llm_unavailable.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("NONEXISTENT-12345")

        assert result["available"] is False
        assert result["url"] is None
        assert result["formats"] == []
        assert result["mpn"] == "NONEXISTENT-12345"

    @pytest.mark.asyncio
    async def test_partial_formats(self):
        """Component found but only some formats available."""
        mock_resp = _mock_response("llm_partial.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("NE555P")

        assert result["available"] is True
        assert result["formats"] == ["kicad"]
        assert "altium" not in result["formats"]

    @pytest.mark.asyncio
    async def test_invalid_llm_response(self):
        """LLM returns text without valid JSON."""
        mock_resp = _mock_response("llm_invalid.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("BADRESPONSE-001")

        assert result["available"] is False
        assert result["url"] is None
        assert result["formats"] == []

    @pytest.mark.asyncio
    async def test_empty_choices(self):
        """LLM returns empty choices array."""
        mock_resp = _mock_response("llm_empty_choices.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("EMPTY-001")

        assert result["available"] is False

    @pytest.mark.asyncio
    async def test_tools_fallback_on_422(self):
        """When tool call returns 422, falls back to simple prompt."""
        error_resp = httpx.Response(
            status_code=422,
            json={"error": "tools not supported"},
            request=httpx.Request("POST", "http://fake/v1/chat/completions"),
        )
        success_resp = _mock_response("llm_available.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError(
                    "422", request=error_resp.request, response=error_resp
                )
            return success_resp

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.side_effect = side_effect
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("STM32F103C8T6")

        assert result["available"] is True
        assert result["confidence"] == "low"  # fallback = low confidence

    @pytest.mark.asyncio
    async def test_network_timeout(self):
        """Network timeout raises and is handled."""
        client = SnapMagicSearchClient(base_url="http://fake")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.side_effect = httpx.ReadTimeout("Connection timed out")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(httpx.ReadTimeout):
                await client.check_availability("TIMEOUT-001")


class TestCheckBatch:
    @pytest.mark.asyncio
    async def test_batch_mixed_results(self):
        """Batch with one available, one unavailable component."""
        available_resp = _mock_response("llm_available.json")
        unavailable_resp = _mock_response("llm_unavailable.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return available_resp
            return unavailable_resp

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.side_effect = side_effect
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await client.check_batch(["STM32F103C8T6", "NONEXISTENT-123"])

        assert len(results) == 2
        assert results[0]["available"] is True
        assert results[0]["mpn"] == "STM32F103C8T6"
        assert results[1]["available"] is False
        assert results[1]["mpn"] == "NONEXISTENT-123"

    @pytest.mark.asyncio
    async def test_batch_with_error(self):
        """Batch where one MPN causes an error -- error is captured, not raised."""
        available_resp = _mock_response("llm_available.json")

        client = SnapMagicSearchClient(base_url="http://fake")

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return available_resp
            raise httpx.ReadTimeout("timed out")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.side_effect = side_effect
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await client.check_batch(["STM32F103C8T6", "TIMEOUT-MPN"])

        assert len(results) == 2
        assert results[0]["available"] is True
        assert results[1]["available"] is False
        assert "error" in results[1]
        assert results[1]["mpn"] == "TIMEOUT-MPN"


# ---------------------------------------------------------------------------
# Tests for server-level tools (format filtering)
# ---------------------------------------------------------------------------

class TestFormatFiltering:
    """Test the _filter_formats helper from server.py."""

    def test_filter_any_passes_through(self):
        from server import _filter_formats

        result = {
            "available": True,
            "url": "https://x.com",
            "formats": ["kicad", "altium"],
        }
        filtered = _filter_formats(result, "any")
        assert filtered["available"] is True

    def test_filter_specific_format_present(self):
        from server import _filter_formats

        result = {
            "available": True,
            "url": "https://x.com",
            "formats": ["kicad", "altium"],
        }
        filtered = _filter_formats(result, "kicad")
        assert filtered["available"] is True

    def test_filter_specific_format_missing(self):
        from server import _filter_formats

        result = {
            "available": True,
            "url": "https://x.com",
            "formats": ["kicad"],
        }
        filtered = _filter_formats(result, "altium")
        assert filtered["available"] is False
