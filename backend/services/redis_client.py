"""Redis client for task queue and pub/sub."""

from __future__ import annotations

import json
import os
from typing import Callable

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()

TASK_QUEUE = "agent:tasks"
STATUS_CHANNEL_PREFIX = "agent:status:"

_client: aioredis.Redis | None = None


def get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        raise RuntimeError("Redis client not initialised — call init() first")
    return _client


async def init() -> aioredis.Redis:
    """Initialise the async Redis client from REDIS_URL."""
    global _client
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _client = aioredis.from_url(url, decode_responses=True)
    # Verify connection
    await _client.ping()
    log.info("redis.initialised", url=url)
    return _client


async def close() -> None:
    """Close the Redis connection."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
        log.info("redis.closed")


async def publish_task(task_payload: dict) -> None:
    """Push a task onto the agent:tasks queue (LPUSH)."""
    client = get_client()
    payload = json.dumps(task_payload)
    await client.lpush(TASK_QUEUE, payload)
    log.info(
        "redis.task.published",
        conversation_id=task_payload.get("conversation_id"),
        task_id=task_payload.get("task_id"),
    )


async def subscribe_status(
    conversation_id: str,
    callback: Callable[[dict], None],
) -> aioredis.client.PubSub:
    """Subscribe to agent status updates for a conversation.

    Returns the PubSub instance so the caller can unsubscribe / close it.
    """
    client = get_client()
    channel = f"{STATUS_CHANNEL_PREFIX}{conversation_id}"
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    log.info("redis.status.subscribed", conversation_id=conversation_id, channel=channel)
    return pubsub


async def get_queue_length() -> int:
    """Return the current length of the task queue."""
    client = get_client()
    length = await client.llen(TASK_QUEUE)
    return length
