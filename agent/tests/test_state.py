"""Tests for StateManager: save/load, pause/resume, cleanup."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from models import OrchestratorState
from state import (
    DECISIONS_KEY_PREFIX,
    PAUSED_LIST_KEY,
    STATE_KEY_PREFIX,
    StateManager,
)


# ---------------------------------------------------------------------------
# In-memory Redis mock with hash and list support for StateManager
# ---------------------------------------------------------------------------


class FakeRedisForState:
    """Minimal Redis mock supporting hset/hgetall/delete/lpush/lrem/lrange/brpop."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}

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

    async def hgetall(self, key: str) -> dict[str, str]:
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

    async def lpush(self, key: str, *values: str) -> int:
        lst = self.lists.setdefault(key, [])
        for v in reversed(values):
            lst.insert(0, v)
        return len(lst)

    async def lrem(self, key: str, count: int, value: str) -> int:
        lst = self.lists.get(key, [])
        removed = 0
        while value in lst and (count == 0 or removed < abs(count)):
            lst.remove(value)
            removed += 1
        return removed

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self.lists.get(key, [])
        if stop == -1:
            return list(lst[start:])
        return list(lst[start : stop + 1])

    async def brpop(self, key: str, timeout: int = 0) -> tuple[str, str] | None:
        lst = self.lists.get(key, [])
        if not lst:
            return None
        item = lst.pop()
        return (key, item)


@pytest.fixture
def fake_redis() -> FakeRedisForState:
    return FakeRedisForState()


@pytest.fixture
def state_manager(fake_redis: FakeRedisForState) -> StateManager:
    return StateManager(fake_redis)


def _make_state(task_id: str = "task-1", phase: str = "searching") -> OrchestratorState:
    return OrchestratorState(
        task_id=task_id,
        conversation_id="conv-1",
        user_id="user-1",
        phase=phase,
        message="Find components",
        production_volume=100,
    )


# ---------------------------------------------------------------------------
# save / load roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_load_roundtrip(state_manager: StateManager) -> None:
    """Saving and loading a state should produce an identical object."""
    original = _make_state()
    await state_manager.save(original)

    loaded = await state_manager.load("task-1")
    assert loaded is not None
    assert loaded.task_id == original.task_id
    assert loaded.conversation_id == original.conversation_id
    assert loaded.phase == original.phase
    assert loaded.production_volume == original.production_volume
    assert loaded.model_dump() == original.model_dump()


@pytest.mark.asyncio
async def test_load_nonexistent_returns_none(state_manager: StateManager) -> None:
    """Loading a task_id that was never saved returns None."""
    result = await state_manager.load("nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# pause adds to paused list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_adds_to_paused_list(
    state_manager: StateManager,
    fake_redis: FakeRedisForState,
) -> None:
    """Pausing a task saves state and adds task_id to the paused list."""
    state = _make_state("task-pause")
    await state_manager.pause(state)

    # State should be persisted
    loaded = await state_manager.load("task-pause")
    assert loaded is not None
    assert loaded.task_id == "task-pause"

    # Task ID should appear in the paused list
    paused = await state_manager.get_paused_task_ids()
    assert "task-pause" in paused

    # paused_at timestamp should be set
    key = f"{STATE_KEY_PREFIX}task-pause"
    data = await fake_redis.hgetall(key)
    assert "paused_at" in data


# ---------------------------------------------------------------------------
# cleanup removes all keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_removes_all_keys(
    state_manager: StateManager,
    fake_redis: FakeRedisForState,
) -> None:
    """Cleanup should remove state hash, paused list entry, and decisions list."""
    state = _make_state("task-clean")
    await state_manager.pause(state)

    # Push a decision so the decisions key exists
    decisions_key = f"{DECISIONS_KEY_PREFIX}task-clean"
    await fake_redis.lpush(decisions_key, json.dumps({"action": "keep"}))

    # Verify everything exists before cleanup
    assert await state_manager.load("task-clean") is not None
    assert "task-clean" in await state_manager.get_paused_task_ids()
    assert len(fake_redis.lists.get(decisions_key, [])) > 0

    # Cleanup
    await state_manager.cleanup("task-clean")

    # Verify everything is gone
    assert await state_manager.load("task-clean") is None
    assert "task-clean" not in await state_manager.get_paused_task_ids()
    assert decisions_key not in fake_redis.lists


# ---------------------------------------------------------------------------
# get_paused_task_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_paused_task_ids_multiple(state_manager: StateManager) -> None:
    """get_paused_task_ids returns all paused task IDs."""
    await state_manager.pause(_make_state("t1"))
    await state_manager.pause(_make_state("t2"))
    await state_manager.pause(_make_state("t3"))

    paused = await state_manager.get_paused_task_ids()
    assert set(paused) == {"t1", "t2", "t3"}


@pytest.mark.asyncio
async def test_get_paused_task_ids_empty(state_manager: StateManager) -> None:
    """get_paused_task_ids returns empty list when nothing is paused."""
    paused = await state_manager.get_paused_task_ids()
    assert paused == []


# ---------------------------------------------------------------------------
# pop_decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pop_decision_returns_decision(
    state_manager: StateManager,
    fake_redis: FakeRedisForState,
) -> None:
    """pop_decision returns a pushed decision dict."""
    key = f"{DECISIONS_KEY_PREFIX}task-dec"
    decision = {"action": "swap", "mpn": "ABC123"}
    await fake_redis.lpush(key, json.dumps(decision))

    result = await state_manager.pop_decision("task-dec")
    assert result is not None
    assert result["action"] == "swap"
    assert result["mpn"] == "ABC123"


@pytest.mark.asyncio
async def test_pop_decision_returns_none_when_empty(
    state_manager: StateManager,
) -> None:
    """pop_decision returns None when no decisions are queued."""
    result = await state_manager.pop_decision("task-empty", timeout=0)
    assert result is None
