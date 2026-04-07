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
        self.hashes: dict[str, dict[str, str]] = {}
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
        # Support both str and bytes matching
        removed = 0
        to_remove = []
        for i, v in enumerate(lst):
            if v == value or (isinstance(v, bytes) and v == value.encode()) or (isinstance(value, bytes) and v == value.decode()):
                to_remove.append(i)
                removed += 1
                if count != 0 and removed >= abs(count):
                    break
        for i in reversed(to_remove):
            lst.pop(i)
        return removed

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def lpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        for v in reversed(values):
            lst.insert(0, v)
        return len(lst)

    async def lrange(self, key: str, start: int, stop: int) -> list:
        lst = self.lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start:stop + 1]

    async def hset(self, key: str, field: str | None = None, value: str | None = None, mapping: dict | None = None) -> int:
        h = self.hashes.setdefault(key, {})
        count = 0
        if mapping:
            for k, v in mapping.items():
                h[k] = v
                count += 1
        if field is not None and value is not None:
            h[field] = value
            count += 1
        return count

    async def hget(self, key: str, field: str) -> str | None:
        h = self.hashes.get(key, {})
        return h.get(field)

    async def hgetall(self, key: str) -> dict:
        return dict(self.hashes.get(key, {}))

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self.hashes:
                del self.hashes[key]
                count += 1
            if key in self.lists:
                del self.lists[key]
                count += 1
        return count

    async def brpop(self, key: str, timeout: int = 0) -> tuple | None:
        lst = self.lists.get(key, [])
        if not lst:
            return None
        item = lst.pop()
        return (key, item)


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


# ---------------------------------------------------------------------------
# Mock Anthropic client
# ---------------------------------------------------------------------------


def make_tool_call(
    tool_use_id: str, name: str, arguments: dict[str, Any]
) -> MagicMock:
    """Build a mock Anthropic ToolUseBlock."""
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_use_id
    block.name = name
    block.input = arguments
    return block


def make_text_block(text: str) -> MagicMock:
    """Build a mock Anthropic TextBlock."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def make_llm_response(
    content: str | None = None,
    tool_calls: list | None = None,
    stop_reason: str = "end_turn",
) -> MagicMock:
    """Build a mock Anthropic Message response."""
    blocks = []
    if content is not None:
        blocks.append(make_text_block(content))
    if tool_calls:
        blocks.extend(tool_calls)

    response = MagicMock()
    response.content = blocks
    response.stop_reason = stop_reason
    return response


@pytest.fixture
def mock_anthropic_client() -> AsyncMock:
    return AsyncMock()


# Keep legacy alias — test_search_agent.py still references it
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
