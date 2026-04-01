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


# ---------------------------------------------------------------------------
# Image tool interception: crop_zoom_image
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crop_zoom_result_intercepted_as_image_url(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """crop_zoom_image base64 is NOT stored in tool result; instead it is
    injected as an image_url content part in a user message on the next
    iteration.
    """
    # Small 1x1 white JPEG as base64 for testing
    import base64
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, format="JPEG")
    tiny_b64 = base64.b64encode(buf.getvalue()).decode()

    crop_result = json.dumps({
        "base64": tiny_b64,
        "minio_path": "minio://temp/crops/page_1_crop.png",
    })

    async def _mock_call_tool(name, args):
        if name == "crop_zoom_image":
            return crop_result
        return '{"ok": true}'

    mock_mcp_router.call_tool = AsyncMock(side_effect=_mock_call_tool)

    # Step 1: LLM wants to crop_zoom
    tc1 = make_tool_call("tc-1", "crop_zoom_image", {
        "image_path": "minio://temp/page_1.png",
        "x1_pct": 0, "y1_pct": 0, "x2_pct": 50, "y2_pct": 50,
    })
    resp1 = make_llm_response(tool_calls=[tc1])

    # Step 2: LLM produces final answer (after seeing the injected image)
    final = {"status": "analysis", "message": "I can see the components.", "data": {}}
    resp2 = make_llm_response(content=json.dumps(final))

    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=[resp1, resp2]
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Zoom into top-left")
    assert result["status"] == "analysis"

    # Verify that the second LLM call received messages including:
    # 1. The tool result should NOT contain the base64 string
    # 2. A user message with image_url should have been injected
    second_call_messages = mock_openai_client.chat.completions.create.call_args_list[1]
    messages = second_call_messages.kwargs.get("messages") or second_call_messages[1].get("messages", [])

    # Find the tool result message
    tool_results = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_results) == 1
    tool_content = tool_results[0]["content"]
    # The tool result should NOT contain base64 data
    assert "base64" not in tool_content.lower() or "image will be visible" in tool_content.lower()

    # Find injected user message with image_url
    user_msgs_with_images = [
        m for m in messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for p in m["content"]
        )
    ]
    assert len(user_msgs_with_images) >= 1


# ---------------------------------------------------------------------------
# Image tool interception: get_image_base64
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_image_base64_intercepted(
    mock_openai_client: AsyncMock,
    mock_mcp_router: AsyncMock,
) -> None:
    """get_image_base64 results are also intercepted and injected as image_url."""
    import base64
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, format="JPEG")
    tiny_b64 = base64.b64encode(buf.getvalue()).decode()

    async def _mock_call_tool(name, args):
        if name == "get_image_base64":
            return json.dumps({"base64": tiny_b64})
        return '{"ok": true}'

    mock_mcp_router.call_tool = AsyncMock(side_effect=_mock_call_tool)

    tc = make_tool_call("tc-1", "get_image_base64", {"image_path": "minio://temp/page_3.png"})
    resp1 = make_llm_response(tool_calls=[tc])

    final = {"status": "analysis", "message": "Page loaded.", "data": {}}
    resp2 = make_llm_response(content=json.dumps(final))

    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=[resp1, resp2]
    )

    runner = AgentRunner(
        litellm_base_url="http://fake:4000",
        mcp_router=mock_mcp_router,
        openai_client=mock_openai_client,
    )

    result = await runner.run(user_message="Load page 3")
    assert result["status"] == "analysis"

    # Verify image_url was injected into messages for the 2nd LLM call
    second_call_messages = mock_openai_client.chat.completions.create.call_args_list[1]
    messages = second_call_messages.kwargs.get("messages") or second_call_messages[1].get("messages", [])

    user_image_msgs = [
        m for m in messages
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
    ]
    assert len(user_image_msgs) >= 1


# ---------------------------------------------------------------------------
# Context trimming
# ---------------------------------------------------------------------------


def test_trim_context_large_tool_results() -> None:
    """_trim_context replaces large tool results with truncated versions."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "tc1"}]},
        {"role": "tool", "tool_call_id": "tc1", "content": "x" * 3000},
        {"role": "assistant", "content": "Done"},
    ]

    AgentRunner._trim_context(messages)

    tool_msg = messages[3]
    assert len(tool_msg["content"]) < 3000
    assert "[trimmed to save context]" in tool_msg["content"]


def test_trim_context_drops_injected_images() -> None:
    """_trim_context removes image_url parts from injected user messages."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc123"}},
            {"type": "text", "text": "[Injected image from crop_zoom]"},
        ]},
        {"role": "assistant", "content": "I see it."},
    ]

    AgentRunner._trim_context(messages)

    user_msg = messages[1]
    parts = user_msg["content"]
    # Should only have text parts remaining
    assert all(p.get("type") == "text" for p in parts)
    assert not any(p.get("type") == "image_url" for p in parts)


def test_trim_context_preserves_small_tool_results() -> None:
    """_trim_context does not touch tool results under the threshold."""
    short_content = '{"status": "ok", "parts": 5}'
    messages = [
        {"role": "tool", "tool_call_id": "tc1", "content": short_content},
    ]

    AgentRunner._trim_context(messages)

    assert messages[0]["content"] == short_content
