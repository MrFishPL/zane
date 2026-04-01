"""Task state serialization for pause/resume."""

import json
import time

import structlog

from models import OrchestratorState

log = structlog.get_logger()

PAUSED_LIST_KEY = "agent:paused"
STATE_KEY_PREFIX = "agent:task_state:"
DECISIONS_KEY_PREFIX = "agent:decisions:"


class StateManager:
    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def save(self, state: OrchestratorState) -> None:
        key = f"{STATE_KEY_PREFIX}{state.task_id}"
        await self._redis.hset(key, mapping={"state": state.model_dump_json()})

    async def load(self, task_id: str) -> OrchestratorState | None:
        key = f"{STATE_KEY_PREFIX}{task_id}"
        data = await self._redis.hgetall(key)
        if not data:
            return None
        raw = data.get("state") or data.get(b"state")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return OrchestratorState.model_validate_json(raw)

    async def pause(self, state: OrchestratorState) -> None:
        await self.save(state)
        await self._redis.hset(f"{STATE_KEY_PREFIX}{state.task_id}", "paused_at", str(time.time()))
        await self._redis.lpush(PAUSED_LIST_KEY, state.task_id)

    async def cleanup(self, task_id: str) -> None:
        await self._redis.delete(f"{STATE_KEY_PREFIX}{task_id}")
        await self._redis.lrem(PAUSED_LIST_KEY, 0, task_id)
        await self._redis.delete(f"{DECISIONS_KEY_PREFIX}{task_id}")

    async def get_paused_task_ids(self) -> list[str]:
        raw = await self._redis.lrange(PAUSED_LIST_KEY, 0, -1)
        return [x.decode() if isinstance(x, bytes) else x for x in raw]

    async def pop_decision(self, task_id: str, timeout: int = 5) -> dict | None:
        key = f"{DECISIONS_KEY_PREFIX}{task_id}"
        result = await self._redis.brpop(key, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
