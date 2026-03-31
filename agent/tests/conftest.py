"""Shared test fixtures for the agent service."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock Redis
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory Redis stand-in for tests."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.published: list[tuple[str, str]] = []  # (channel, payload)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass

    async def blmove(
        self,
        first_list: str,
        second_list: str,
        timeout: int = 0,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        src_list = self.lists.get(first_list, [])
        if not src_list:
            return None
        if src == "LEFT":
            item = src_list.pop(0)
        else:
            item = src_list.pop()
        dst_list = self.lists.setdefault(second_list, [])
        if dest == "RIGHT":
            dst_list.append(item)
        else:
            dst_list.insert(0, item)
        return item

    async def rpoplpush(self, src: str, dst: str) -> str | None:
        src_list = self.lists.get(src, [])
        if not src_list:
            return None
        item = src_list.pop()
        dst_list = self.lists.setdefault(dst, [])
        dst_list.insert(0, item)
        return item

    async def lrem(self, key: str, count: int, value: str) -> int:
        lst = self.lists.get(key, [])
        removed = 0
        while value in lst and (count == 0 or removed < abs(count)):
            lst.remove(value)
            removed += 1
        return removed

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def lpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        for v in reversed(values):
            lst.insert(0, v)
        return len(lst)


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


# ---------------------------------------------------------------------------
# Mock OpenAI client
# ---------------------------------------------------------------------------


def make_tool_call(
    tool_call_id: str, name: str, arguments: dict[str, Any]
) -> MagicMock:
    tc = MagicMock()
    tc.id = tool_call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


def make_llm_response(
    content: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str = "stop",
) -> MagicMock:
    """Build a mock OpenAI chat completion response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    message.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": (
            [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
            if tool_calls
            else None
        ),
    }

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.fixture
def mock_openai_client() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# Mock MCP router
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mcp_router() -> AsyncMock:
    router = AsyncMock()
    router.call_tool = AsyncMock(return_value='{"ok": true}')
    router.close = AsyncMock()
    return router
