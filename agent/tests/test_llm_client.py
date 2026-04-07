"""Tests for agent/llm_client.py — JSON parsing, timeout retry, tool calling."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_client import LLMClient, _parse_json, _strip_code_fences

# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


class TestStripCodeFences:
    def test_no_fences(self):
        assert _strip_code_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert _strip_code_fences(text) == '{"a": 1}'

    def test_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        assert _strip_code_fences(text) == '{"a": 1}'

    def test_fence_with_extra_whitespace(self):
        text = '```json\n  {"a": 1}  \n```'
        assert _strip_code_fences(text) == '{"a": 1}'

    def test_multiline_json_in_fence(self):
        text = '```json\n{\n  "components": [\n    {"ref": "R1"}\n  ]\n}\n```'
        result = _strip_code_fences(text)
        parsed = json.loads(result)
        assert parsed["components"][0]["ref"] == "R1"


class TestParseJson:
    def test_plain_json(self):
        result = _parse_json('{"status": "found"}')
        assert result == {"status": "found"}

    def test_fenced_json(self):
        result = _parse_json('```json\n{"status": "found"}\n```')
        assert result == {"status": "found"}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("not json at all")


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


def _make_response(content: str = '{"ok": true}', tool_calls=None):
    """Build a mock Anthropic Message response."""
    blocks = []
    if content is not None:
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = content
        blocks.append(text_block)
    if tool_calls:
        blocks.extend(tool_calls)

    response = MagicMock()
    response.content = blocks
    response.stop_reason = "tool_use" if tool_calls else "end_turn"
    return response


@pytest.mark.asyncio
class TestLLMClientChat:
    async def test_successful_call(self):
        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")
        mock_response = _make_response('{"result": "ok"}')
        client._client.messages.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.chat(
            [{"role": "user", "content": "hello"}]
        )
        assert result.content[0].text == '{"result": "ok"}'

    async def test_tool_calls_passthrough(self):
        """Verify tool definitions are forwarded to the API."""
        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "call_1"
        tool_block.name = "search_parts"
        tool_block.input = {"query": "8.2k 0603"}

        mock_response = _make_response(content=None, tool_calls=[tool_block])
        client._client.messages.create = AsyncMock(
            return_value=mock_response
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_parts",
                    "description": "Search for parts",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        result = await client.chat(
            [{"role": "user", "content": "find 8.2k resistor"}],
            tools=tools,
        )

        # Verify tools were passed to the API
        call_kwargs = client._client.messages.create.call_args
        assert "tools" in call_kwargs.kwargs

        # Verify response has tool_use blocks
        tool_use_blocks = [b for b in result.content if b.type == "tool_use"]
        assert len(tool_use_blocks) == 1
        assert tool_use_blocks[0].name == "search_parts"

    async def test_timeout_retry_escalation(self):
        """On timeout, should retry with higher timeout."""
        from anthropic import APITimeoutError

        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")

        timeout_error = APITimeoutError(request=MagicMock())
        success_response = _make_response('{"ok": true}')

        client._client.messages.create = AsyncMock(
            side_effect=[timeout_error, success_response]
        )

        result = await client.chat(
            [{"role": "user", "content": "test"}],
            timeouts=[10, 20, 30],
        )

        assert result.content[0].text == '{"ok": true}'
        assert client._client.messages.create.call_count == 2

        # Verify second call used higher timeout
        calls = client._client.messages.create.call_args_list
        assert calls[0].kwargs["timeout"] == 10
        assert calls[1].kwargs["timeout"] == 20

    async def test_all_timeouts_exhausted_raises(self):
        """If all timeouts are exhausted, the error propagates."""
        from anthropic import APITimeoutError

        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")

        timeout_error = APITimeoutError(request=MagicMock())
        client._client.messages.create = AsyncMock(
            side_effect=[timeout_error, timeout_error, timeout_error]
        )

        with pytest.raises(APITimeoutError):
            await client.chat(
                [{"role": "user", "content": "test"}],
                timeouts=[10, 20, 30],
            )

        assert client._client.messages.create.call_count == 3


@pytest.mark.asyncio
class TestLLMClientAnalyzeSchematic:
    async def test_analyze_returns_parsed_json(self):
        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")

        response_content = json.dumps({
            "production_volume": 5,
            "priority": "price",
            "components": [
                {"ref": "R1", "type": "resistor", "value": "8.2k"}
            ],
        })
        mock_response = _make_response(response_content)
        client._client.messages.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.analyze_schematic(
            system_prompt="Analyze this schematic",
            user_text="Build an audio mixer, 5 units",
            image_urls=["data:image/png;base64,abc123"],
        )

        assert result["production_volume"] == 5
        assert len(result["components"]) == 1
        assert result["components"][0]["ref"] == "R1"

    async def test_analyze_strips_code_fences(self):
        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")

        fenced = '```json\n{"components": []}\n```'
        mock_response = _make_response(fenced)
        client._client.messages.create = AsyncMock(
            return_value=mock_response
        )

        result = await client.analyze_schematic(
            system_prompt="test",
            user_text="test",
        )

        assert result == {"components": []}

    async def test_analyze_uses_phase2_timeouts(self):
        """analyze_schematic should use PHASE2_TIMEOUTS."""
        from llm_client import PHASE2_TIMEOUTS

        client = LLMClient(api_key="test-key", model="claude-sonnet-4-6")

        mock_response = _make_response('{"components": []}')
        client._client.messages.create = AsyncMock(
            return_value=mock_response
        )

        await client.analyze_schematic(
            system_prompt="test",
            user_text="test",
        )

        call_kwargs = client._client.messages.create.call_args.kwargs
        assert call_kwargs["timeout"] == PHASE2_TIMEOUTS[0]
