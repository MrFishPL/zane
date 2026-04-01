"""WebSocket manager — bridges Redis pub/sub to connected clients."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from services import redis_client, supabase_client, task_manager

log = structlog.get_logger()


class ConnectionManager:
    """Manages active WebSocket connections per conversation."""

    def __init__(self):
        # conversation_id -> set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = {}
        # conversation_id -> asyncio.Task running the Redis listener
        self._listeners: dict[str, asyncio.Task] = {}

    async def connect(self, websocket: WebSocket, conversation_id: str) -> None:
        """Accept a WebSocket and subscribe to Redis pub/sub for this conversation."""
        await websocket.accept()

        if conversation_id not in self._connections:
            self._connections[conversation_id] = set()
        self._connections[conversation_id].add(websocket)

        log.info(
            "ws.connected",
            conversation_id=conversation_id,
            active=len(self._connections[conversation_id]),
        )

        # Send current agent status if a task is running
        task = supabase_client.get_agent_task(conversation_id)
        if task is not None:
            await websocket.send_json({
                "type": "status",
                "task_id": task["id"],
                "status": task["status"],
                "current_status": task.get("current_status"),
            })

        # Start Redis listener if not already running for this conversation
        if conversation_id not in self._listeners or self._listeners[conversation_id].done():
            self._listeners[conversation_id] = asyncio.create_task(
                self._redis_listener(conversation_id)
            )

    async def disconnect(self, websocket: WebSocket, conversation_id: str) -> None:
        """Remove a WebSocket connection and clean up if no more clients."""
        conns = self._connections.get(conversation_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                del self._connections[conversation_id]
                # Cancel Redis listener if no more connections
                listener = self._listeners.pop(conversation_id, None)
                if listener and not listener.done():
                    listener.cancel()

        log.info(
            "ws.disconnected",
            conversation_id=conversation_id,
            remaining=len(self._connections.get(conversation_id, set())),
        )

    async def broadcast(self, conversation_id: str, message: dict) -> None:
        """Send a message to all WebSocket connections for a conversation."""
        conns = self._connections.get(conversation_id)
        if not conns:
            return

        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)

        # Clean up dead connections
        for ws in dead:
            conns.discard(ws)
        if not conns:
            del self._connections[conversation_id]

    async def _redis_listener(self, conversation_id: str) -> None:
        """Listen to Redis pub/sub and forward messages to WebSocket clients."""
        pubsub = None
        try:
            pubsub = await redis_client.subscribe_status(conversation_id, callback=None)

            while conversation_id in self._connections:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message.get("type") == "message":
                    try:
                        data = json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        data = {"raw": message["data"]}

                    # Note: result persistence is handled by the background
                    # listener in messages.py — the WS manager only broadcasts
                    await self.broadcast(conversation_id, data)

        except asyncio.CancelledError:
            log.info("ws.redis_listener.cancelled", conversation_id=conversation_id)
        except Exception as exc:
            log.error("ws.redis_listener.error", conversation_id=conversation_id, error=str(exc))
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception:
                    pass
            log.info("ws.redis_listener.stopped", conversation_id=conversation_id)


# Singleton instance
manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket, conversation_id: str) -> None:
    """WebSocket endpoint handler."""
    await manager.connect(websocket, conversation_id)
    try:
        while True:
            # Keep connection alive; we don't expect client messages,
            # but we need to read to detect disconnection.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, conversation_id)
