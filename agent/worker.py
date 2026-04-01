"""Redis queue consumer that delegates to the Orchestrator."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import redis.asyncio as aioredis
import structlog

from llm_client import LLMClient
from mcp_router import MCPRouter
from models import AgentResult, OrchestratorState
from state import StateManager

log = structlog.get_logger()


class AgentWorker:
    """Consumes tasks from Redis, delegates to Orchestrator."""

    def __init__(self, redis_url: str, max_concurrent: int = 50) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._llm = LLMClient()
        self._router = MCPRouter()
        self._state_mgr: StateManager | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._redis_url, decode_responses=False)
        self._state_mgr = StateManager(self._redis)
        log.info("worker.connected", redis_url=self._redis_url)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Main loop: consume tasks and listen for decisions."""
        assert self._redis is not None

        requeued = await self._requeue_orphaned_tasks()
        if requeued:
            log.info("worker.requeued_orphaned", count=requeued)

        # Recover paused tasks on startup
        await self._recover_paused_tasks()

        # Start decision listener in background
        decision_task = asyncio.create_task(self._decision_listener(shutdown_event))

        try:
            while not shutdown_event.is_set():
                try:
                    result = await self._redis.blmove(
                        "agent:tasks", "agent:processing", 1, "LEFT", "RIGHT",
                    )
                except Exception:
                    if shutdown_event.is_set():
                        break
                    await asyncio.sleep(1)
                    continue

                if result is None:
                    continue

                raw = result.decode() if isinstance(result, bytes) else result
                task = json.loads(raw)
                await self._semaphore.acquire()
                t = asyncio.create_task(self._process_task_wrapper(task, raw))
                t.add_done_callback(lambda _: self._semaphore.release())
        finally:
            decision_task.cancel()
            try:
                await decision_task
            except asyncio.CancelledError:
                pass

    async def _process_task_wrapper(self, task: dict, raw_task: str) -> None:
        """Wrapper to ensure exceptions don't crash the event loop."""
        try:
            await self._process_task(task, raw_task)
        except Exception as e:
            import traceback
            log.error("worker.process_task_wrapper_error", error=str(e), tb=traceback.format_exc())

    async def _process_task(self, task: dict, raw_task: str) -> None:
        """Process a single task via the Orchestrator."""
        assert self._redis is not None

        task_id = task.get("task_id", "unknown")
        conversation_id = task.get("conversation_id", "unknown")

        try:
            # Import here to allow orchestrator to not exist yet at import time
            from orchestrator import Orchestrator

            log.info("worker.processing_task", task_id=task_id,
                     attachments=task.get("attachments", []),
                     message_preview=task.get("message", "")[:200])

            orch = Orchestrator(self._llm, self._router, self._state_mgr, self._publish)
            result = await orch.run(
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=task.get("user_id", ""),
                message=task.get("message", ""),
                attachments=task.get("attachments", []),
                conversation_history=task.get("conversation_history"),
            )

            if result.status == "decision_required":
                # Pause: save state, move to paused list
                state = OrchestratorState(**result.data["state"])
                await self._state_mgr.pause(state)
                # Publish only the decisions, not the full state (too large for pub/sub)
                decision_msg = {
                    "status": "decision_required",
                    "task_id": task_id,
                    "message": result.message,
                    "decisions": [d.model_dump() for d in result.decisions] if result.decisions else [],
                }
                await self._publish(
                    conversation_id, task_id, "decision_required", decision_msg,
                )
                # Move from processing to paused
                await self._redis.lrem("agent:processing", 1, raw_task)
                log.info("worker.task_paused", task_id=task_id, num_decisions=len(decision_msg["decisions"]))
                return

            await self._publish(conversation_id, task_id, "result", result.model_dump())
            log.info("worker.task_completed", task_id=task_id, status=result.status)

        except Exception as e:
            import traceback
            log.error("worker.task_error", task_id=task_id, error=str(e)[:500], tb=traceback.format_exc())
            try:
                await self._publish(
                    conversation_id, task_id, "error", {"error": str(e)[:500]},
                )
            except Exception:
                log.error("worker.publish_error_failed", task_id=task_id)
        finally:
            await self._redis.lrem("agent:processing", 1, raw_task)

    async def _decision_listener(self, shutdown_event: asyncio.Event) -> None:
        """Background loop: poll paused tasks for user decisions + auto-timeout."""
        assert self._state_mgr is not None
        assert self._redis is not None

        while not shutdown_event.is_set():
            try:
                paused_ids = await self._state_mgr.get_paused_task_ids()
                for task_id in paused_ids:
                    # Check for auto-timeout (30 minutes)
                    state = await self._state_mgr.load(task_id)
                    if state:
                        paused_at = await self._redis.hget(
                            f"agent:task_state:{task_id}", "paused_at"
                        )
                        if paused_at:
                            ts = float(
                                paused_at.decode()
                                if isinstance(paused_at, bytes)
                                else paused_at
                            )
                            elapsed = time.time() - ts
                            if elapsed > 1800:  # 30 minutes
                                log.info(
                                    "worker.auto_timeout",
                                    task_id=task_id,
                                    elapsed_s=int(elapsed),
                                )
                                # Auto-select first option for each decision
                                auto_decisions = {
                                    d.decision_id: d.options[0].key
                                    for d in state.decisions
                                    if d.options
                                }
                                asyncio.create_task(
                                    self._resume_task(task_id, auto_decisions)
                                )
                                continue

                    decision = await self._state_mgr.pop_decision(task_id, timeout=1)
                    if decision:
                        asyncio.create_task(
                            self._resume_task(task_id, decision)
                        )
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("worker.decision_listener_error", error=str(e)[:200])
            await asyncio.sleep(2)

    async def _resume_task(self, task_id: str, user_decisions: dict) -> None:
        """Resume a paused task with user decisions."""
        try:
            await self._semaphore.acquire()
            try:
                state = await self._state_mgr.load(task_id)
                if not state:
                    log.warning("worker.resume_no_state", task_id=task_id)
                    return

                from orchestrator import Orchestrator

                orch = Orchestrator(
                    self._llm, self._router, self._state_mgr, self._publish
                )
                result = await orch.resume(state, user_decisions)
                await self._publish(
                    state.conversation_id, task_id, "result", result.model_dump()
                )
            finally:
                self._semaphore.release()
        except Exception as e:
            log.error("worker.resume_error", task_id=task_id, error=str(e)[:500])

    async def _publish(
        self,
        conversation_id: str,
        task_id: str,
        msg_type: str,
        data: Any = None,
    ) -> None:
        """Publish status/result/error to Redis pub/sub."""
        assert self._redis is not None
        message = json.dumps({
            "task_id": task_id,
            "type": msg_type,
            **({"text": data} if isinstance(data, str) else {"data": data} if data else {}),
        })
        await self._redis.publish(f"agent:status:{conversation_id}", message)

    async def _requeue_orphaned_tasks(self) -> int:
        """Move any tasks stuck in processing back to the queue."""
        assert self._redis is not None
        count = 0
        while True:
            task = await self._redis.rpoplpush("agent:processing", "agent:tasks")
            if task is None:
                break
            count += 1
        return count

    async def _recover_paused_tasks(self) -> None:
        """On startup, check paused tasks and re-publish decision requests."""
        assert self._state_mgr is not None

        paused_ids = await self._state_mgr.get_paused_task_ids()
        for task_id in paused_ids:
            decision = await self._state_mgr.pop_decision(task_id, timeout=0)
            if decision:
                asyncio.create_task(self._resume_task(task_id, decision))
            else:
                state = await self._state_mgr.load(task_id)
                if state and state.decisions:
                    await self._publish(
                        state.conversation_id, task_id, "decision_required",
                        AgentResult(
                            status="decision_required",
                            task_id=task_id,
                            message="Waiting for your input on component decisions.",
                            decisions=state.decisions,
                        ).model_dump(),
                    )
