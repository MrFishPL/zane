"""Tests for the new Redis-backed AgentWorker (orchestrator-based)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker import AgentWorker
from models import AgentResult, OrchestratorState, Decision, DecisionOption
from state import StateManager
from tests.conftest import FakeRedis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "t1",
    conversation_id: str = "conv-1",
    message: str = "Find me a 100nF 0402 capacitor",
) -> dict:
    return {
        "task_id": task_id,
        "conversation_id": conversation_id,
        "message": message,
        "attachments": [],
        "conversation_history": [],
    }


def _make_worker(fake_redis: FakeRedis) -> AgentWorker:
    """Create a worker instance wired to a FakeRedis."""
    worker = AgentWorker.__new__(AgentWorker)
    worker._redis_url = "redis://fake:6379/0"
    worker._redis = fake_redis
    worker._semaphore = asyncio.Semaphore(10)
    worker._llm = MagicMock()
    worker._router = MagicMock()
    worker._state_mgr = StateManager(fake_redis)
    return worker


# ---------------------------------------------------------------------------
# Tests: orphaned task recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requeue_orphaned_tasks(fake_redis: FakeRedis) -> None:
    """Orphaned tasks in processing queue are moved back to the task queue."""
    task_raw = json.dumps(_make_task(task_id="orphan-1"))
    fake_redis.lists["agent:processing"] = [task_raw]

    worker = _make_worker(fake_redis)
    count = await worker._requeue_orphaned_tasks()

    assert count == 1
    assert fake_redis.lists["agent:tasks"] == [task_raw]
    assert fake_redis.lists["agent:processing"] == []


@pytest.mark.asyncio
async def test_requeue_multiple_orphaned_tasks(fake_redis: FakeRedis) -> None:
    """Multiple orphaned tasks are all requeued."""
    tasks = [json.dumps(_make_task(task_id=f"orphan-{i}")) for i in range(3)]
    fake_redis.lists["agent:processing"] = list(tasks)

    worker = _make_worker(fake_redis)
    count = await worker._requeue_orphaned_tasks()

    assert count == 3
    assert len(fake_redis.lists["agent:tasks"]) == 3


@pytest.mark.asyncio
async def test_requeue_no_orphans(fake_redis: FakeRedis) -> None:
    """No-op when there are no orphaned tasks."""
    worker = _make_worker(fake_redis)
    count = await worker._requeue_orphaned_tasks()
    assert count == 0


# ---------------------------------------------------------------------------
# Tests: task processing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_success(fake_redis: FakeRedis) -> None:
    """Successful task publishes result and removes from processing."""
    task = _make_task()
    raw_task = json.dumps(task)
    raw_task_bytes = raw_task.encode()
    fake_redis.lists["agent:processing"] = [raw_task]

    agent_result = AgentResult(
        status="recommendation",
        message="Found it",
        data={"components": []},
    )

    mock_orch = AsyncMock()
    mock_orch.run = AsyncMock(return_value=agent_result)

    worker = _make_worker(fake_redis)

    with patch("worker.Orchestrator", return_value=mock_orch):
        await worker._process_task(task, raw_task, raw_task_bytes)

    # Should have published a result
    assert len(fake_redis.published) >= 1
    result_msgs = [
        json.loads(p[1]) for p in fake_redis.published
        if json.loads(p[1])["type"] == "result"
    ]
    assert len(result_msgs) == 1

    # Should have been removed from processing queue
    assert raw_task not in fake_redis.lists.get("agent:processing", [])


@pytest.mark.asyncio
async def test_process_task_error(fake_redis: FakeRedis) -> None:
    """When the orchestrator raises, an error message is published."""
    task = _make_task()
    raw_task = json.dumps(task)
    raw_task_bytes = raw_task.encode()
    fake_redis.lists["agent:processing"] = [raw_task]

    mock_orch = AsyncMock()
    mock_orch.run = AsyncMock(side_effect=RuntimeError("LLM exploded"))

    worker = _make_worker(fake_redis)

    with patch("worker.Orchestrator", return_value=mock_orch):
        await worker._process_task(task, raw_task, raw_task_bytes)

    # Should have published an error
    error_msgs = [
        json.loads(p[1]) for p in fake_redis.published
        if json.loads(p[1])["type"] == "error"
    ]
    assert len(error_msgs) == 1
    assert "LLM exploded" in error_msgs[0]["data"]["error"]


@pytest.mark.asyncio
async def test_process_task_decision_required(fake_redis: FakeRedis) -> None:
    """When orchestrator returns decision_required, state is saved and published."""
    task = _make_task(task_id="t-decision")
    raw_task = json.dumps(task)
    raw_task_bytes = raw_task.encode()
    fake_redis.lists["agent:processing"] = [raw_task]

    state_dict = OrchestratorState(
        task_id="t-decision",
        conversation_id="conv-1",
        user_id="user-1",
        phase="sourcing",
        decisions=[
            Decision(
                decision_id="d1",
                ref="R1",
                issue="low stock",
                question="Pick an alternative?",
                options=[
                    DecisionOption(key="opt_a", label="Option A", mpn="MPN-A"),
                    DecisionOption(key="opt_b", label="Option B", mpn="MPN-B"),
                ],
            ),
        ],
    ).model_dump()

    agent_result = AgentResult(
        status="decision_required",
        task_id="t-decision",
        message="Need your input",
        data={"state": state_dict},
        decisions=[
            Decision(
                decision_id="d1",
                ref="R1",
                issue="low stock",
                question="Pick an alternative?",
                options=[
                    DecisionOption(key="opt_a", label="Option A", mpn="MPN-A"),
                    DecisionOption(key="opt_b", label="Option B", mpn="MPN-B"),
                ],
            ),
        ],
    )

    mock_orch = AsyncMock()
    mock_orch.run = AsyncMock(return_value=agent_result)

    worker = _make_worker(fake_redis)

    with patch("worker.Orchestrator", return_value=mock_orch):
        await worker._process_task(task, raw_task, raw_task_bytes)

    # Worker always publishes as "result" type; the decision_required status
    # lives inside the result payload itself.
    result_msgs = [
        json.loads(p[1]) for p in fake_redis.published
        if json.loads(p[1])["type"] == "result"
    ]
    assert len(result_msgs) == 1
    assert result_msgs[0]["data"]["status"] == "decision_required"

    # Task should have been removed from processing
    assert raw_task not in fake_redis.lists.get("agent:processing", [])


# ---------------------------------------------------------------------------
# Tests: publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_string_data(fake_redis: FakeRedis) -> None:
    """Publishing string data uses 'text' key."""
    worker = _make_worker(fake_redis)
    await worker._publish("conv-1", "t1", "status", "Processing...")

    assert len(fake_redis.published) == 1
    channel, payload_str = fake_redis.published[0]
    assert channel == "agent:status:conv-1"
    payload = json.loads(payload_str)
    assert payload["task_id"] == "t1"
    assert payload["type"] == "status"
    assert payload["text"] == "Processing..."


@pytest.mark.asyncio
async def test_publish_dict_data(fake_redis: FakeRedis) -> None:
    """Publishing dict data uses 'data' key."""
    worker = _make_worker(fake_redis)
    await worker._publish("conv-1", "t1", "result", {"status": "ok"})

    assert len(fake_redis.published) == 1
    payload = json.loads(fake_redis.published[0][1])
    assert payload["data"] == {"status": "ok"}


@pytest.mark.asyncio
async def test_publish_no_data(fake_redis: FakeRedis) -> None:
    """Publishing with no data produces a minimal message."""
    worker = _make_worker(fake_redis)
    await worker._publish("conv-1", "t1", "status")

    payload = json.loads(fake_redis.published[0][1])
    assert payload["task_id"] == "t1"
    assert payload["type"] == "status"
    assert "data" not in payload
    assert "text" not in payload


# ---------------------------------------------------------------------------
# Tests: recover paused tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_paused_tasks_with_pending_decision(fake_redis: FakeRedis) -> None:
    """On startup, paused tasks with pending decisions get resumed."""
    worker = _make_worker(fake_redis)

    # Simulate a paused task with a pending decision
    state = OrchestratorState(
        task_id="t-paused",
        conversation_id="conv-1",
        user_id="user-1",
        phase="sourcing",
        decisions=[
            Decision(
                decision_id="d1",
                ref="R1",
                issue="low stock",
                question="Pick?",
                options=[DecisionOption(key="a", label="A")],
            ),
        ],
    )
    await worker._state_mgr.pause(state)

    # Push a decision
    decision_data = json.dumps({"decision_id": "d1", "choice": "a"})
    fake_redis.lists.setdefault("agent:decisions:t-paused", []).append(decision_data)

    # Mock _resume_task to verify it gets called
    worker._resume_task = AsyncMock()

    await worker._recover_paused_tasks()

    # Give the create_task a moment to schedule
    await asyncio.sleep(0.05)

    # _resume_task should have been called
    # (it's fired via asyncio.create_task, so we need to let the loop run)
    # Since we mocked it, we can check if it was called
    worker._resume_task.assert_called_once()


@pytest.mark.asyncio
async def test_recover_paused_tasks_republishes_decision_required(fake_redis: FakeRedis) -> None:
    """On startup, paused tasks without pending decisions re-publish decision_required."""
    worker = _make_worker(fake_redis)

    state = OrchestratorState(
        task_id="t-paused-2",
        conversation_id="conv-2",
        user_id="user-1",
        phase="sourcing",
        decisions=[
            Decision(
                decision_id="d1",
                ref="R1",
                issue="low stock",
                question="Pick?",
                options=[DecisionOption(key="a", label="A")],
            ),
        ],
    )
    await worker._state_mgr.pause(state)

    await worker._recover_paused_tasks()

    # Should have published decision_required
    decision_msgs = [
        json.loads(p[1]) for p in fake_redis.published
        if json.loads(p[1])["type"] == "decision_required"
    ]
    assert len(decision_msgs) == 1
    assert decision_msgs[0]["data"]["status"] == "decision_required"


# ---------------------------------------------------------------------------
# Tests: run loop integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_picks_task_and_processes(fake_redis: FakeRedis) -> None:
    """The run loop picks a task, processes it, and stops on shutdown."""
    task = _make_task()
    task_raw = json.dumps(task)
    fake_redis.lists["agent:tasks"] = [task_raw]

    agent_result = AgentResult(
        status="analysis", message="Done", data={}
    )

    mock_orch = AsyncMock()
    mock_orch.run = AsyncMock(return_value=agent_result)

    worker = _make_worker(fake_redis)

    shutdown = asyncio.Event()

    # Patch blmove to trigger shutdown after the task is consumed
    original_blmove = fake_redis.blmove
    _call_count = 0

    async def _counting_blmove(*args, **kwargs):
        nonlocal _call_count
        _call_count += 1
        result = await original_blmove(*args, **kwargs)
        # After the first real task is picked and None is returned, shut down
        if result is None and _call_count >= 2:
            await asyncio.sleep(0.1)
            shutdown.set()
        return result

    fake_redis.blmove = _counting_blmove

    with patch("worker.Orchestrator", return_value=mock_orch):
        await asyncio.wait_for(worker.run(shutdown), timeout=5.0)

    # Verify result was published
    result_msgs = [
        json.loads(p[1]) for p in fake_redis.published
        if json.loads(p[1])["type"] == "result"
    ]
    assert len(result_msgs) == 1
