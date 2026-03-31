"""Tests for AgentRunner: LLM loop + MCP tool orchestration."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agent_runner import AgentRunner
from tests.conftest import make_llm_response, make_tool_call


# ---------------------------------------------------------------------------
# Direct final answer (no tool calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_answer_json(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """LLM returns valid JSON immediately without tool calls."""
    answer = {
        "status": "analysis",
        "message": "I see a power supply block with three capacitors.",
        "data": {
            "blocks": [{"name": "Power Supply", "components": ["C1", "C2", "C3"], "page": 1}],
            "identified_components": ["C1", "C2", "C3"],
            "unclear_areas": [],
        },
    }

    mock_openai_client.chat.completions.create = AsyncMock(
        return_value=make_llm_response(content=json.dumps(answer))
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Analyze this schematic")

    assert result["status"] == "analysis"
    assert len(result["data"]["blocks"]) == 1
    mock_mcp_router.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Tool call: search_parts -> final answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_parts_then_answer(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """LLM calls search_parts, gets result, then produces a recommendation."""
    search_result = json.dumps(
        {
            "parts": [
                {
                    "mpn": "GRM155R71C104KA88D",
                    "manufacturer": "Murata",
                    "description": "100nF 0402 MLCC",
                    "price": 0.01,
                    "stock": 50000,
                }
            ]
        }
    )
    mock_mcp_router.call_tool = AsyncMock(return_value=search_result)

    # First call: LLM wants to search_parts
    tool_call = make_tool_call("tc-1", "search_parts", {"query": "100nF 0402 capacitor"})
    first_response = make_llm_response(tool_calls=[tool_call])

    # Second call: LLM returns final recommendation
    final_answer = {
        "status": "recommendation",
        "message": "Found a suitable 100nF capacitor.",
        "data": {
            "components": [
                {
                    "ref": "C1",
                    "mpn": "GRM155R71C104KA88D",
                    "manufacturer": "Murata",
                    "description": "100nF 0402 MLCC",
                    "package": "0402",
                    "qty_per_unit": 1,
                    "qty_total": 1,
                    "justification": "Lowest price in stock",
                    "unit_price": 0.01,
                    "price_break": "1+",
                    "stock": 50000,
                    "lifecycle": "Active",
                    "distributor": "Digi-Key",
                    "distributor_url": None,
                    "datasheet_url": None,
                    "snapmagic_url": None,
                    "snapmagic_available": False,
                    "snapmagic_formats": [],
                    "mpn_confidence": "verified",
                    "verified": True,
                    "warnings": [],
                    "alternatives": [],
                }
            ],
            "not_sourced": [],
            "bom_summary": {
                "unique_parts": 1,
                "total_components_per_unit": 1,
                "cost_per_unit": 0.01,
                "cost_total": 0.01,
                "volume": 1,
                "currency": "USD",
            },
            "export_files": {"csv": None, "kicad_library": None, "altium_library": None},
            "sources_queried": ["Nexar"],
        },
    }
    second_response = make_llm_response(content=json.dumps(final_answer))

    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=[first_response, second_response]
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Find me a 100nF 0402 capacitor")

    assert result["status"] == "recommendation"
    assert result["data"]["components"][0]["mpn"] == "GRM155R71C104KA88D"

    # Verify the tool was called
    mock_mcp_router.call_tool.assert_called_once_with(
        "search_parts", {"query": "100nF 0402 capacitor"}
    )


# ---------------------------------------------------------------------------
# Multiple tool calls in sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_then_cad_check(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """LLM calls search_parts, then check_cad_availability, then answers."""

    async def _mock_call_tool(name, args):
        if name == "search_parts":
            return json.dumps({"parts": [{"mpn": "LM7805", "manufacturer": "TI"}]})
        if name == "check_cad_availability":
            return json.dumps({"available": True, "formats": ["KiCad", "Altium"]})
        return json.dumps({"ok": True})

    mock_mcp_router.call_tool = AsyncMock(side_effect=_mock_call_tool)

    # Step 1: search_parts
    tc1 = make_tool_call("tc-1", "search_parts", {"query": "LM7805"})
    resp1 = make_llm_response(tool_calls=[tc1])

    # Step 2: check_cad_availability
    tc2 = make_tool_call("tc-2", "check_cad_availability", {"mpn": "LM7805"})
    resp2 = make_llm_response(tool_calls=[tc2])

    # Step 3: final answer
    final = {"status": "recommendation", "message": "Here you go.", "data": {"components": []}}
    resp3 = make_llm_response(content=json.dumps(final))

    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=[resp1, resp2, resp3]
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Find LM7805 with CAD models")

    assert result["status"] == "recommendation"
    assert mock_mcp_router.call_tool.call_count == 2


# ---------------------------------------------------------------------------
# Tool call error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_error_reported_to_llm(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """When a tool call fails, the error is fed back to the LLM."""
    mock_mcp_router.call_tool = AsyncMock(
        side_effect=ConnectionError("mcp-nexar unreachable")
    )

    tc = make_tool_call("tc-1", "search_parts", {"query": "test"})
    resp1 = make_llm_response(tool_calls=[tc])

    # After receiving the error, LLM produces a clarification
    final = {
        "status": "needs_clarification",
        "message": "The component search service is currently unavailable.",
        "data": {"questions": [], "annotated_image": None},
    }
    resp2 = make_llm_response(content=json.dumps(final))

    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=[resp1, resp2]
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Find parts")

    assert result["status"] == "needs_clarification"


# ---------------------------------------------------------------------------
# Needs clarification response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_clarification(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """LLM returns needs_clarification when schematic is unclear."""
    answer = {
        "status": "needs_clarification",
        "message": "I cannot read the value of R3. Could you specify it?",
        "data": {
            "questions": [
                {"id": "q1", "question": "What is the value of R3?", "default": "10k"}
            ],
            "annotated_image": None,
        },
    }

    mock_openai_client.chat.completions.create = AsyncMock(
        return_value=make_llm_response(content=json.dumps(answer))
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Source parts for this board")

    assert result["status"] == "needs_clarification"
    assert len(result["data"]["questions"]) == 1


# ---------------------------------------------------------------------------
# Non-JSON LLM answer is wrapped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_answer_wrapped(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """Plain text LLM response is wrapped in analysis status."""
    mock_openai_client.chat.completions.create = AsyncMock(
        return_value=make_llm_response(content="I found three resistors on the board.")
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Look at this")

    assert result["status"] == "analysis"
    assert "three resistors" in result["message"]


# ---------------------------------------------------------------------------
# Status callback invoked during tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_callback_called(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """on_status callback fires for each tool call."""
    mock_mcp_router.call_tool = AsyncMock(return_value='{"ok": true}')

    tc = make_tool_call("tc-1", "search_parts", {"query": "cap"})
    resp1 = make_llm_response(tool_calls=[tc])
    final = {"status": "recommendation", "message": "Done", "data": {}}
    resp2 = make_llm_response(content=json.dumps(final))

    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=[resp1, resp2]
    )

    statuses: list[str] = []

    async def _on_status(text: str) -> None:
        statuses.append(text)

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    await runner.run(user_message="Go", on_status=_on_status)

    assert len(statuses) == 1
    assert "search_parts" in statuses[0]
