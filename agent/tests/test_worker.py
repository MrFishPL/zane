"""Tests for the Redis-backed AgentWorker."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from worker import AgentWorker, QUEUE_TASKS, QUEUE_PROCESSING, STATUS_PREFIX
from tests.conftest import FakeRedis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "t1",
    conversation_id: str = "conv-1",
    message: str = "Find me a 100nF 0402 capacitor",
) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "message": message,
            "history": [],
            "attachments": [],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requeue_orphaned_tasks(fake_redis: FakeRedis) -> None:
    """Orphaned tasks in processing queue are moved back to the task queue."""
    task = _make_task(task_id="orphan-1")
    fake_redis.lists[QUEUE_PROCESSING] = [task]

    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = None

    count = await worker.requeue_orphaned_tasks()

    assert count == 1
    assert fake_redis.lists[QUEUE_TASKS] == [task]
    assert fake_redis.lists[QUEUE_PROCESSING] == []

    # Verify a status update was published
    assert len(fake_redis.published) == 1
    channel, payload_str = fake_redis.published[0]
    assert channel == f"{STATUS_PREFIX}conv-1"
    payload = json.loads(payload_str)
    assert payload["type"] == "status"
    assert "Requeued" in payload["text"]


@pytest.mark.asyncio
async def test_requeue_multiple_orphaned_tasks(fake_redis: FakeRedis) -> None:
    """Multiple orphaned tasks are all requeued."""
    tasks = [_make_task(task_id=f"orphan-{i}") for i in range(3)]
    fake_redis.lists[QUEUE_PROCESSING] = list(tasks)

    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = None

    count = await worker.requeue_orphaned_tasks()
    assert count == 3
    assert len(fake_redis.lists[QUEUE_TASKS]) == 3


@pytest.mark.asyncio
async def test_requeue_no_orphans(fake_redis: FakeRedis) -> None:
    """No-op when there are no orphaned tasks."""
    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = None

    count = await worker.requeue_orphaned_tasks()
    assert count == 0


@pytest.mark.asyncio
async def test_process_task_success(fake_redis: FakeRedis) -> None:
    """Successful task processing publishes status + result and removes from processing."""
    task_raw = _make_task()
    fake_redis.lists[QUEUE_PROCESSING] = [task_raw]

    result_data = {
        "status": "recommendation",
        "message": "Found it",
        "data": {"components": []},
    }

    mock_runner = AsyncMock()
    mock_runner.run = AsyncMock(return_value=result_data)
    mock_runner._router = AsyncMock()
    mock_runner._router.call_tool = AsyncMock()

    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = mock_runner
    worker._semaphore = asyncio.Semaphore(10)

    await worker.process_task(task_raw)

    # Should have published at least "Processing" + result
    assert len(fake_redis.published) >= 2
    types = [json.loads(p[1])["type"] for p in fake_redis.published]
    assert "status" in types
    assert "result" in types

    # Should have been removed from processing queue
    assert task_raw not in fake_redis.lists.get(QUEUE_PROCESSING, [])


@pytest.mark.asyncio
async def test_process_task_error(fake_redis: FakeRedis) -> None:
    """When the agent raises, an error message is published."""
    task_raw = _make_task()
    fake_redis.lists[QUEUE_PROCESSING] = [task_raw]

    mock_runner = AsyncMock()
    mock_runner.run = AsyncMock(side_effect=RuntimeError("LLM exploded"))
    mock_runner._router = AsyncMock()
    mock_runner._router.call_tool = AsyncMock()

    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = mock_runner
    worker._semaphore = asyncio.Semaphore(10)

    await worker.process_task(task_raw)

    # Should have published an error
    error_msgs = [
        json.loads(p[1]) for p in fake_redis.published if json.loads(p[1])["type"] == "error"
    ]
    assert len(error_msgs) == 1
    assert "LLM exploded" in error_msgs[0]["error"]


@pytest.mark.asyncio
async def test_process_task_malformed_json(fake_redis: FakeRedis) -> None:
    """Malformed task JSON still publishes an error, doesn't crash."""
    task_raw = "not valid json"

    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = AsyncMock()
    worker._semaphore = asyncio.Semaphore(10)

    await worker.process_task(task_raw)

    # Should have published an error
    assert any(
        json.loads(p[1])["type"] == "error" for p in fake_redis.published
    )


@pytest.mark.asyncio
async def test_run_picks_task_and_processes(fake_redis: FakeRedis) -> None:
    """The run loop picks a task, processes it, and stops on shutdown."""
    task_raw = _make_task()
    fake_redis.lists[QUEUE_TASKS] = [task_raw]

    result_data = {"status": "analysis", "message": "Done", "data": {}}

    mock_runner = AsyncMock()
    mock_runner.run = AsyncMock(return_value=result_data)
    mock_runner._router = AsyncMock()
    mock_runner._router.call_tool = AsyncMock()

    worker = AgentWorker.__new__(AgentWorker)
    worker._redis = fake_redis
    worker._runner = mock_runner
    worker._max_concurrent = 5
    worker._semaphore = asyncio.Semaphore(5)

    shutdown = asyncio.Event()

    # Patch blmove so it yields control and eventually triggers shutdown
    original_blmove = fake_redis.blmove
    _call_count = 0

    async def _counting_blmove(*args, **kwargs):
        nonlocal _call_count
        _call_count += 1
        result = await original_blmove(*args, **kwargs)
        # After the first real task is picked and None is returned, shut down
        if result is None and _call_count >= 2:
            # Give in-flight processing a moment, then signal
            await asyncio.sleep(0.1)
            shutdown.set()
        return result

    fake_redis.blmove = _counting_blmove

    await asyncio.wait_for(worker.run(shutdown), timeout=5.0)

    # Verify result was published
    result_msgs = [
        json.loads(p[1]) for p in fake_redis.published if json.loads(p[1])["type"] == "result"
    ]
    assert len(result_msgs) == 1
