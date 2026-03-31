"""Message creation and task submission endpoint."""

from __future__ import annotations

import asyncio
import os

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import supabase_client, minio_client, task_manager

log = structlog.get_logger()

router = APIRouter(prefix="/api/conversations", tags=["messages"])

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


class SendMessageRequest(BaseModel):
    content: str
    attachments: list[dict] | None = None
    upload_ids: list[str] | None = None


@router.post("/{conversation_id}/messages", status_code=202)
async def send_message(conversation_id: str, body: SendMessageRequest):
    """Send a user message and submit an agent task.

    Returns 202 Accepted immediately. The agent processes asynchronously.
    Returns 409 Conflict if an agent task is already running.
    """
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

    # Generate title in background if this is the first message
    if len(messages) <= 1:
        asyncio.create_task(
            _generate_title(conversation_id, body.content)
        )

    return {
        "message": message,
        "task_id": task["id"],
        "status": "accepted",
    }


async def _generate_title(conversation_id: str, first_message: str) -> None:
    """Generate a conversation title using LiteLLM (gpt-4o-mini) in the background."""
    litellm_url = os.environ.get("LITELLM_BASE_URL", "http://litellm-proxy:4000")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{litellm_url}/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Generate a short, descriptive title (max 50 chars) for a "
                                "conversation about electronic component sourcing. "
                                "The title should capture the user's intent. "
                                "Return ONLY the title text, no quotes or punctuation."
                            ),
                        },
                        {"role": "user", "content": first_message},
                    ],
                    "max_tokens": 30,
                    "temperature": 0.5,
                },
            )
            response.raise_for_status()
            data = response.json()
            title = data["choices"][0]["message"]["content"].strip()
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
