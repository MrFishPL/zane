"""Tests for SearchAgent: focused component search sub-agent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import ComponentSpec
from search_agent import SearchAgent
from tests.conftest import make_llm_response, make_tool_call


def _make_spec(
    ref: str = "C1",
    comp_type: str = "capacitor",
    value: str = "100nF",
    package: str = "0402",
) -> ComponentSpec:
    return ComponentSpec(ref=ref, type=comp_type, value=value, package=package)


def _make_llm_mock(*, side_effect=None, return_value=None) -> AsyncMock:
    """Build an LLM mock whose ``.chat(...)`` is an AsyncMock.

    SearchAgent calls ``self._llm.chat(messages, tools=...)``, so we need
    ``llm.chat`` to be an awaitable returning Anthropic-format responses.
    """
    llm = AsyncMock()
    if side_effect is not None:
        llm.chat = AsyncMock(side_effect=side_effect)
    elif return_value is not None:
        llm.chat = AsyncMock(return_value=return_value)
    return llm


# ---------------------------------------------------------------------------
# Direct find (no tools) — LLM returns answer immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_find_no_tools(
    mock_mcp_router: AsyncMock,
) -> None:
    """LLM returns a SearchResult directly without calling any tools."""
    answer = {
        "status": "found",
        "ref": "C1",
        "mpn": "GRM155R71C104KA88D",
        "manufacturer": "Murata",
        "description": "100nF 0402 MLCC",
        "unit_price": 0.01,
        "currency": "USD",
        "total_stock": 50000,
        "distributor": "Digi-Key",
        "distributor_url": "https://www.digikey.com/product-detail/en/GRM155R71C104KA88D",
    }

    llm = _make_llm_mock(return_value=make_llm_response(content=json.dumps(answer)))

    agent = SearchAgent(
        llm_client=llm,
        mcp_router=mock_mcp_router,
        max_iterations=10,
    )

    result = await agent.search(_make_spec())

    assert result.status == "found"
    assert result.ref == "C1"
    assert result.mpn == "GRM155R71C104KA88D"
    assert result.manufacturer == "Murata"
    assert result.unit_price == 0.01
    mock_mcp_router.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Tool call + final answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_then_answer(
    mock_mcp_router: AsyncMock,
) -> None:
    """LLM calls search_parts, gets result, then returns a found SearchResult."""
    search_result = json.dumps({
        "parts": [
            {
                "mpn": "RC0402FR-0710KL",
                "manufacturer": "Yageo",
                "description": "10k 0402 resistor",
                "price": 0.002,
                "stock": 100000,
            }
        ]
    })
    mock_mcp_router.call_tool = AsyncMock(return_value=search_result)

    # First LLM call: wants to search_parts
    tc = make_tool_call("tc-1", "search_parts", {"query": "10k 0402 resistor"})
    first_response = make_llm_response(tool_calls=[tc])

    # Second LLM call: returns final answer
    final_answer = {
        "status": "found",
        "ref": "R1",
        "mpn": "RC0402FR-0710KL",
        "manufacturer": "Yageo",
        "description": "10k 0402 resistor",
        "unit_price": 0.002,
        "currency": "USD",
        "total_stock": 100000,
        "distributor": "Digi-Key",
    }
    second_response = make_llm_response(content=json.dumps(final_answer))

    llm = _make_llm_mock(side_effect=[first_response, second_response])

    agent = SearchAgent(
        llm_client=llm,
        mcp_router=mock_mcp_router,
    )

    result = await agent.search(
        _make_spec(ref="R1", comp_type="resistor", value="10k"),
    )

    assert result.status == "found"
    assert result.mpn == "RC0402FR-0710KL"
    assert result.ref == "R1"
    mock_mcp_router.call_tool.assert_called_once_with(
        "search_parts", {"query": "10k 0402 resistor"}
    )


# ---------------------------------------------------------------------------
# Max iterations reached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_iterations_reached(
    mock_mcp_router: AsyncMock,
) -> None:
    """When the LLM keeps calling tools without producing an answer,
    SearchAgent returns an error after max_iterations.
    """
    mock_mcp_router.call_tool = AsyncMock(
        return_value=json.dumps({"parts": []})
    )

    # Every LLM call returns a tool call, never a final answer
    tc = make_tool_call("tc-loop", "search_parts", {"query": "unobtainium"})
    tool_response = make_llm_response(tool_calls=[tc])

    llm = _make_llm_mock(return_value=tool_response)

    max_iter = 3
    agent = SearchAgent(
        llm_client=llm,
        mcp_router=mock_mcp_router,
        max_iterations=max_iter,
    )

    result = await agent.search(_make_spec(ref="X1", comp_type="unknown", value="???"))

    assert result.status == "error"
    assert result.ref == "X1"
    assert "maximum iterations" in (result.reason or "").lower()
    assert llm.chat.call_count == max_iter


# ---------------------------------------------------------------------------
# Tool result truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_tool_result_truncated(
    mock_mcp_router: AsyncMock,
) -> None:
    """Tool results exceeding 50K chars are truncated."""
    large_result = "x" * 60_000
    mock_mcp_router.call_tool = AsyncMock(return_value=large_result)

    tc = make_tool_call("tc-big", "search_parts", {"query": "big"})
    first_response = make_llm_response(tool_calls=[tc])

    final_answer = {"status": "not_found", "ref": "C1", "reason": "Too much data"}
    second_response = make_llm_response(content=json.dumps(final_answer))

    llm = _make_llm_mock(side_effect=[first_response, second_response])

    agent = SearchAgent(
        llm_client=llm,
        mcp_router=mock_mcp_router,
    )

    result = await agent.search(_make_spec())

    assert result.status == "not_found"

    # Verify the tool result passed to the LLM was truncated.
    # SearchAgent appends tool results as:
    #   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}
    second_call = llm.chat.call_args_list[1]
    messages = second_call[0][0]  # first positional arg = messages list
    tool_result_msgs = [
        m for m in messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert len(tool_result_msgs) == 1
    tool_result_block = tool_result_msgs[0]["content"][0]
    assert len(tool_result_block["content"]) <= 50_000 + len("...[truncated]")
    assert tool_result_block["content"].endswith("...[truncated]")


# ---------------------------------------------------------------------------
# Tool call error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_fed_back(
    mock_mcp_router: AsyncMock,
) -> None:
    """When a tool call raises an exception, the error is reported back to the LLM."""
    mock_mcp_router.call_tool = AsyncMock(
        side_effect=ConnectionError("mcp-nexar down")
    )

    tc = make_tool_call("tc-err", "search_parts", {"query": "fail"})
    first_response = make_llm_response(tool_calls=[tc])

    final_answer = {"status": "error", "ref": "C1", "reason": "Search service unavailable"}
    second_response = make_llm_response(content=json.dumps(final_answer))

    llm = _make_llm_mock(side_effect=[first_response, second_response])

    agent = SearchAgent(
        llm_client=llm,
        mcp_router=mock_mcp_router,
    )

    result = await agent.search(_make_spec())

    assert result.status == "error"

    # The second LLM call should have received an error in the tool result.
    # Tool results are in {"role": "user", "content": [{"type": "tool_result", ...}]}
    second_call = llm.chat.call_args_list[1]
    messages = second_call[0][0]
    tool_result_msgs = [
        m for m in messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert len(tool_result_msgs) == 1
    tool_result_content = tool_result_msgs[0]["content"][0]["content"]
    assert "mcp-nexar down" in tool_result_content


# ---------------------------------------------------------------------------
# Parse answer: non-JSON fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_answer_returns_error(
    mock_mcp_router: AsyncMock,
) -> None:
    """When the LLM returns non-JSON, SearchAgent returns an error result."""
    llm = _make_llm_mock(
        return_value=make_llm_response(content="I could not find any matching parts.")
    )

    agent = SearchAgent(
        llm_client=llm,
        mcp_router=mock_mcp_router,
    )

    result = await agent.search(_make_spec())

    assert result.status == "error"
    assert result.ref == "C1"
    assert "parse" in (result.reason or "").lower()
