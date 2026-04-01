"""Task manager — orchestrates the agent task lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from services import supabase_client, redis_client

log = structlog.get_logger()

MAX_HISTORY_PAIRS = 20


async def submit_task(
    conversation_id: str,
    message_id: str,
    user_id: str,
    message_text: str,
    attachments: list[dict] | None = None,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Create an agent_task row and push it onto the Redis queue.

    Returns the created agent_task row.
    """
    # 1. Create task in Supabase
    task = supabase_client.create_agent_task(conversation_id, message_id)

    # 2. Prepare trimmed history
    history = prepare_conversation_history(conversation_history or [])

    # 3. Build payload
    payload = {
        "task_id": task["id"],
        "conversation_id": conversation_id,
        "message_id": message_id,
        "user_id": user_id,
        "message": message_text,
        "attachments": attachments or [],
        "conversation_history": history,
    }

    # 4. Push to Redis
    await redis_client.publish_task(payload)

    log.info(
        "task_manager.task.submitted",
        task_id=task["id"],
        conversation_id=conversation_id,
        history_length=len(history),
    )
    return task


async def submit_decision(task_id: str, decision_data: dict) -> None:
    """Push a user decision to the agent's decision queue."""
    redis = redis_client.get_client()
    key = f"agent:decisions:{task_id}"
    await redis.lpush(key, json.dumps(decision_data))
    log.info("task_manager.decision_submitted", task_id=task_id)


def prepare_conversation_history(messages: list[dict]) -> list[dict]:
    """Trim and sanitize conversation history for the agent.

    - Keep at most MAX_HISTORY_PAIRS recent message pairs (user + assistant).
    - For recommendation responses: include message + bom_compact only.
    - For other responses: include status + message only.
    - Never include file contents or base64 data — only MinIO paths.
    """
    if not messages:
        return []

    # Take the most recent messages (up to 2 * MAX_HISTORY_PAIRS)
    trimmed = messages[-(MAX_HISTORY_PAIRS * 2):]

    result: list[dict] = []
    for msg in trimmed:
        entry: dict = {
            "role": msg.get("role", "user"),
            "message": msg.get("content", ""),
        }

        # Handle assistant messages with structured content
        content = msg.get("content", "")
        if msg.get("role") == "assistant" and isinstance(content, (dict, str)):
            if isinstance(content, dict):
                status = content.get("status")
                if status == "recommendation":
                    # For recommendations, keep message + bom_compact
                    entry["message"] = content.get("message", "")
                    if content.get("bom_compact"):
                        entry["bom_compact"] = content["bom_compact"]
                else:
                    # For other structured responses, keep status + message
                    entry["message"] = content.get("message", "")
                    if status:
                        entry["status"] = status

        # Include attachments as MinIO paths only (no file contents / base64)
        attachments = msg.get("attachments")
        if attachments:
            entry["attachments"] = [
                {"path": att.get("path", ""), "filename": att.get("filename") or att.get("name", "")}
                for att in attachments
                if att.get("path")
            ]

        result.append(entry)

    return result


async def handle_status_update(
    task_id: str,
    conversation_id: str,
    update: dict,
) -> None:
    """Process an agent status update.

    update.type can be:
      - "status"  : intermediate status text
      - "result"  : final result — save as assistant message, mark task completed
      - "error"   : failure — mark task failed
    """
    update_type = update.get("type")

    if update_type == "status":
        current_status = update.get("current_status", "")
        supabase_client.update_agent_task(task_id, current_status=current_status)
        log.info(
            "task_manager.status.update",
            task_id=task_id,
            current_status=current_status,
        )

    elif update_type == "result":
        # Check if task is already completed (avoid duplicate saves)
        existing = supabase_client.get_agent_task(conversation_id)
        if existing and existing.get("id") == task_id and existing.get("status") == "completed":
            log.info("task_manager.result.already_completed", task_id=task_id)
            return

        # Save assistant message
        content = update.get("content", "")
        attachments = update.get("attachments", [])
        supabase_client.create_message(
            conversation_id,
            role="assistant",
            content=content,
            attachments=attachments,
        )
        # Mark task completed
        supabase_client.update_agent_task(
            task_id,
            status="completed",
            current_status="done",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        log.info("task_manager.task.completed", task_id=task_id)

    elif update_type == "decision_required":
        # Save as assistant message but don't mark task completed — it's paused
        content = update.get("content", "")
        supabase_client.create_message(
            conversation_id,
            role="assistant",
            content=content,
        )
        supabase_client.update_agent_task(
            task_id,
            current_status="Waiting for your decision...",
        )
        log.info("task_manager.task.decision_required", task_id=task_id)

    elif update_type == "error":
        error_msg = update.get("error", "Unknown error")
        supabase_client.update_agent_task(
            task_id,
            status="failed",
            error=error_msg,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        log.error("task_manager.task.failed", task_id=task_id, error=error_msg)

    else:
        log.warning("task_manager.unknown_update_type", task_id=task_id, type=update_type)
