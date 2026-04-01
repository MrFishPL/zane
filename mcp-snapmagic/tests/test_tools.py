"""Unit tests for mcp-snapmagic tools with mocked Tavily responses."""

import asyncio
import pathlib
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Add parent dir to path so imports resolve when running from repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from search_client import SnapMagicSearchClient, _detect_formats, _mpn_matches


# ---------------------------------------------------------------------------
# Helper: build a fake Tavily search response
# ---------------------------------------------------------------------------

def _tavily_response(results: list[dict], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json={"results": results},
        request=httpx.Request("POST", "https://api.tavily.com/search"),
    )


def _snapeda_result(mpn: str, formats_text: str = "KiCad Altium Eagle") -> dict:
    """Build a fake Tavily result that looks like a SnapEDA parts page."""
    return {
        "url": f"https://www.snapeda.com/parts/{mpn}/Manufacturer/view/",
        "title": f"{mpn} - SnapEDA",
        "content": f"{mpn} symbol and footprint. Download in {formats_text} format.",
    }


# ---------------------------------------------------------------------------
# Unit tests for _mpn_matches
# ---------------------------------------------------------------------------

class TestMpnMatches:
    def test_exact_match_in_content(self):
        assert _mpn_matches("STM32F103", "https://snapeda.com/parts/STM32F103/x/", "STM32F103 symbol")

    def test_url_prefix_match(self):
        assert _mpn_matches("BFP740H6327XTSA1", "https://snapeda.com/parts/BFP740/Infineon/", "BFP740 footprint")

    def test_no_match(self):
        assert not _mpn_matches("TOTALLY_DIFFERENT", "https://snapeda.com/parts/ABC123/x/", "ABC123 symbol")


# ---------------------------------------------------------------------------
# Unit tests for _detect_formats
# ---------------------------------------------------------------------------

class TestDetectFormats:
    def test_all_formats(self):
        formats = _detect_formats("Download KiCad, Altium and Eagle files")
        assert formats == ["altium", "eagle", "kicad"]

    def test_single_format(self):
        formats = _detect_formats("Available for KiCad only")
        assert formats == ["kicad"]

    def test_no_formats_but_download_keyword(self):
        formats = _detect_formats("Download symbol and footprint")
        assert formats == ["altium", "eagle", "kicad"]

    def test_no_formats_no_keywords(self):
        formats = _detect_formats("This is a random page about electronics")
        assert formats == []


# ---------------------------------------------------------------------------
# Integration tests for SnapMagicSearchClient.check_availability
# ---------------------------------------------------------------------------

class TestCheckAvailability:
    @pytest.mark.asyncio
    async def test_available_component(self):
        """Component found on SnapEDA with all formats."""
        tavily_resp = _tavily_response([_snapeda_result("STM32F103C8T6")])
        client = SnapMagicSearchClient(api_key="test-key")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = tavily_resp
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
        """Component not found on SnapEDA."""
        tavily_resp = _tavily_response([])
        client = SnapMagicSearchClient(api_key="test-key")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = tavily_resp
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
        tavily_resp = _tavily_response([_snapeda_result("NE555P", "KiCad")])
        client = SnapMagicSearchClient(api_key="test-key")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = tavily_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("NE555P")

        assert result["available"] is True
        assert result["formats"] == ["kicad"]
        assert "altium" not in result["formats"]

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        """Missing API key returns unavailable with low confidence."""
        client = SnapMagicSearchClient(api_key="")

        result = await client.check_availability("STM32F103C8T6")

        assert result["available"] is False
        assert result["confidence"] == "low"

    @pytest.mark.asyncio
    async def test_network_error(self):
        """Network error returns unavailable with error field."""
        client = SnapMagicSearchClient(api_key="test-key")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.side_effect = httpx.ReadTimeout("Connection timed out")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("TIMEOUT-001")

        assert result["available"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_non_parts_url_skipped(self):
        """Results without /parts/ in URL are skipped."""
        non_parts_result = {
            "url": "https://www.snapeda.com/about/",
            "title": "About SnapEDA",
            "content": "STM32F103C8T6 mentioned on about page",
        }
        tavily_resp = _tavily_response([non_parts_result])
        client = SnapMagicSearchClient(api_key="test-key")

        with patch("search_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post.return_value = tavily_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await client.check_availability("STM32F103C8T6")

        assert result["available"] is False


# ---------------------------------------------------------------------------
# Tests for check_batch: dict-by-MPN return format and parallel execution
# ---------------------------------------------------------------------------

class TestCheckBatch:
    @pytest.mark.asyncio
    async def test_batch_returns_dict_keyed_by_mpn(self):
        """Batch returns a dict keyed by MPN, not a list."""
        client = SnapMagicSearchClient(api_key="test-key")

        async def mock_check(mpn):
            return {"available": True, "url": f"https://snapeda.com/parts/{mpn}/x/", "formats": ["kicad"], "confidence": "high", "mpn": mpn}

        with patch.object(client, "check_availability", side_effect=mock_check):
            results = await client.check_batch(["MPN-A", "MPN-B", "MPN-C"])

        assert isinstance(results, dict)
        assert set(results.keys()) == {"MPN-A", "MPN-B", "MPN-C"}
        assert results["MPN-A"]["available"] is True
        assert results["MPN-B"]["mpn"] == "MPN-B"

    @pytest.mark.asyncio
    async def test_batch_mixed_results(self):
        """Batch with one available, one unavailable component."""
        client = SnapMagicSearchClient(api_key="test-key")

        async def mock_check(mpn):
            if mpn == "STM32F103C8T6":
                return {"available": True, "url": "https://snapeda.com/parts/STM32F103C8T6/x/", "formats": ["kicad", "altium", "eagle"], "confidence": "high", "mpn": mpn}
            return {"available": False, "url": None, "formats": [], "confidence": "high", "mpn": mpn}

        with patch.object(client, "check_availability", side_effect=mock_check):
            results = await client.check_batch(["STM32F103C8T6", "NONEXISTENT-123"])

        assert len(results) == 2
        assert results["STM32F103C8T6"]["available"] is True
        assert results["STM32F103C8T6"]["mpn"] == "STM32F103C8T6"
        assert results["NONEXISTENT-123"]["available"] is False
        assert results["NONEXISTENT-123"]["mpn"] == "NONEXISTENT-123"

    @pytest.mark.asyncio
    async def test_batch_skips_exceptions(self):
        """Batch where one MPN causes an exception -- that MPN is skipped."""
        client = SnapMagicSearchClient(api_key="test-key")

        async def mock_check(mpn):
            if mpn == "BROKEN-MPN":
                raise httpx.ReadTimeout("timed out")
            return {"available": True, "url": f"https://snapeda.com/parts/{mpn}/x/", "formats": ["kicad"], "confidence": "high", "mpn": mpn}

        with patch.object(client, "check_availability", side_effect=mock_check):
            results = await client.check_batch(["GOOD-MPN", "BROKEN-MPN"])

        assert isinstance(results, dict)
        assert "GOOD-MPN" in results
        assert "BROKEN-MPN" not in results
        assert results["GOOD-MPN"]["available"] is True

    @pytest.mark.asyncio
    async def test_batch_runs_concurrently(self):
        """Verify that batch calls run concurrently via asyncio.gather."""
        client = SnapMagicSearchClient(api_key="test-key")
        call_order = []

        async def mock_check(mpn):
            call_order.append(f"start-{mpn}")
            await asyncio.sleep(0)  # yield to event loop
            call_order.append(f"end-{mpn}")
            return {"available": True, "url": None, "formats": [], "confidence": "high", "mpn": mpn}

        with patch.object(client, "check_availability", side_effect=mock_check):
            results = await client.check_batch(["A", "B", "C"])

        assert len(results) == 3
        # All starts should happen before any ends (concurrent scheduling)
        start_indices = [call_order.index(f"start-{m}") for m in ["A", "B", "C"]]
        end_indices = [call_order.index(f"end-{m}") for m in ["A", "B", "C"]]
        # With gather, all tasks start before the first one completes
        assert max(start_indices) < min(end_indices)

    @pytest.mark.asyncio
    async def test_batch_empty_list(self):
        """Batch with empty list returns empty dict."""
        client = SnapMagicSearchClient(api_key="test-key")
        results = await client.check_batch([])
        assert results == {}


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
