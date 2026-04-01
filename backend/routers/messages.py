"""Message creation and task submission endpoint."""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services import supabase_client, minio_client, task_manager

log = structlog.get_logger()

router = APIRouter(prefix="/api/conversations", tags=["messages"])

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


class SendMessageRequest(BaseModel):
    content: str = ""
    attachments: list[dict] | None = None
    upload_ids: list[str] | None = None
    # Decision response fields (optional)
    decision_id: str | None = None
    task_id: str | None = None
    choice: str | None = None


@router.post("/{conversation_id}/messages", status_code=202)
async def send_message(conversation_id: str, body: SendMessageRequest):
    """Send a user message and submit an agent task.

    Returns 202 Accepted immediately. The agent processes asynchronously.
    Returns 409 Conflict if an agent task is already running.
    """
    # Handle decision responses — route to agent's decision queue, not a new task
    if body.decision_id:
        if not body.task_id or not body.choice:
            raise HTTPException(400, "decision_id requires task_id and choice")
        # choice may be a JSON string of all decisions (batch mode)
        try:
            choices = json.loads(body.choice)
        except (json.JSONDecodeError, TypeError):
            choices = {body.decision_id: body.choice}
        await task_manager.submit_decision(body.task_id, choices)
        # Don't create a visible user message for decisions
        return JSONResponse({"type": "decision_response", "status": "ok"})

    # Check for running task
    existing_task = supabase_client.get_agent_task(conversation_id)
    if existing_task is not None:
        raise HTTPException(
            status_code=409,
            detail="Agent is already processing a task for this conversation",
        )

    # Move staging files to conversation path if needed
    resolved_attachments = body.attachments or []
    if body.upload_ids:
        for upload_id in body.upload_ids:
            src_prefix = f"{DEFAULT_USER_ID}/staging/{upload_id}"
            dst_prefix = f"{DEFAULT_USER_ID}/{conversation_id}"
            try:
                moved = minio_client.move_files("uploads", src_prefix, "uploads", dst_prefix)
                for path in moved:
                    resolved_attachments.append({"path": path, "upload_id": upload_id})
            except Exception as exc:
                log.warning(
                    "messages.staging_move.error",
                    upload_id=upload_id,
                    error=str(exc),
                )

    # Get conversation history BEFORE adding the new message (avoids duplication)
    messages = supabase_client.get_messages(conversation_id)

    # Save user message
    message = supabase_client.create_message(
        conversation_id,
        role="user",
        content=body.content,
        attachments=resolved_attachments if resolved_attachments else None,
    )

    # Submit task
    task = await task_manager.submit_task(
        conversation_id=conversation_id,
        message_id=message["id"],
        user_id=DEFAULT_USER_ID,
        message_text=body.content,
        attachments=resolved_attachments,
        conversation_history=messages,
    )

    # Generate title immediately for new conversations
    if len(messages) == 0:
        asyncio.create_task(
            _generate_title(conversation_id, body.content)
        )

    # Background listener to persist agent results even without WebSocket
    asyncio.create_task(
        _listen_for_result(task["id"], conversation_id)
    )

    return {
        "message": message,
        "task_id": task["id"],
        "status": "accepted",
    }


async def _generate_title(conversation_id: str, first_message: str) -> None:
    """Generate a conversation title using OpenAI directly in the background."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Generate a short title (max 6 words) for this conversation. "
                                "Return only the title, no quotes."
                            ),
                        },
                        {"role": "user", "content": first_message[:500]},
                    ],
                    "max_tokens": 20,
                },
            )
            data = response.json()
            title = data["choices"][0]["message"]["content"].strip().strip('"')
            if title:
                supabase_client.update_conversation(conversation_id, title)
                log.info(
                    "messages.title.generated",
                    conversation_id=conversation_id,
                    title=title,
                )
    except Exception as exc:
        log.warning(
            "messages.title.generation_failed",
            conversation_id=conversation_id,
            error=str(exc),
        )


async def _listen_for_result(task_id: str, conversation_id: str) -> None:
    """Background listener: persist agent result even without WebSocket."""
    import json as _json
    from services import redis_client as _redis

    try:
        pubsub = await _redis.subscribe_status(conversation_id, callback=None)
        timeout = 600  # 10 min max wait
        import time
        start = time.monotonic()

        while time.monotonic() - start < timeout:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message or message.get("type") != "message":
                continue

            try:
                data = _json.loads(message["data"])
            except (ValueError, TypeError):
                continue

            msg_type = data.get("type")
            msg_task_id = data.get("task_id")

            if msg_task_id != task_id:
                continue

            if msg_type in ("result", "error", "decision_required"):
                update = {"type": msg_type}
                if msg_type == "result":
                    update["content"] = data.get("data", "")
                elif msg_type == "error":
                    update["error"] = data.get("error", "")
                elif msg_type == "decision_required":
                    update["content"] = data.get("data", "")
                try:
                    await task_manager.handle_status_update(task_id, conversation_id, update)
                    log.info("messages.result_listener.saved", task_id=task_id, type=msg_type)
                except Exception as exc:
                    log.error("messages.result_listener.save_failed", task_id=task_id, error=str(exc))
                # Don't break on decision_required — keep listening for the final result after resume
                if msg_type != "decision_required":
                    break
            elif msg_type == "status":
                update = {"type": "status", "current_status": data.get("text", "")}
                try:
                    await task_manager.handle_status_update(task_id, conversation_id, update)
                except Exception:
                    pass

        await pubsub.unsubscribe()
        await pubsub.close()
    except Exception as exc:
        log.warning("messages.result_listener.error", task_id=task_id, error=str(exc))
