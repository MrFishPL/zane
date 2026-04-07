"""Conversation CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import structlog

from services import supabase_client, minio_client
from config import DEFAULT_USER_ID

log = structlog.get_logger()

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str


@router.post("", status_code=201)
def create_conversation(body: CreateConversationRequest | None = None):
    """Create a new conversation."""
    title = body.title if body else None
    conv = supabase_client.create_conversation(user_id=DEFAULT_USER_ID, title=title)
    return conv


@router.get("")
def list_conversations():
    """List all conversations with agent status."""
    conversations = supabase_client.get_conversations(user_id=DEFAULT_USER_ID)
    return conversations


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str):
    """Get a conversation with its messages."""
    try:
        conv = supabase_client.get_conversation(conversation_id)
    except Exception as exc:
        error_msg = str(exc)
        if "not found" in error_msg.lower() or "0 rows" in error_msg.lower():
            raise HTTPException(status_code=404, detail="Conversation not found")
        log.error("conversations.get.error", conversation_id=conversation_id, error=error_msg)
        raise HTTPException(status_code=500, detail="Internal server error")
    return conv


@router.patch("/{conversation_id}")
def update_conversation(conversation_id: str, body: UpdateConversationRequest):
    """Update conversation title."""
    try:
        conv = supabase_client.update_conversation(conversation_id, body.title)
    except Exception as exc:
        log.error("conversations.update.error", conversation_id=conversation_id, error=str(exc))
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str):
    """Delete a conversation and its MinIO files."""
    # Delete MinIO files for this conversation across all buckets
    for bucket in ["uploads", "temp", "exports"]:
        prefix = f"{DEFAULT_USER_ID}/{conversation_id}/"
        try:
            minio_client.delete_prefix(bucket, prefix)
        except Exception as exc:
            log.warning(
                "conversations.delete.minio_error",
                bucket=bucket,
                prefix=prefix,
                error=str(exc),
            )

    # Delete the conversation row (cascading deletes messages + agent_tasks)
    try:
        supabase_client.delete_conversation(conversation_id)
    except Exception as exc:
        log.error("conversations.delete.error", conversation_id=conversation_id, error=str(exc))
        raise HTTPException(status_code=404, detail="Conversation not found")

    return None


@router.get("/{conversation_id}/agent-status")
def get_agent_status(conversation_id: str):
    """Get the current agent task status for a conversation."""
    task = supabase_client.get_agent_task(conversation_id)
    if task is None:
        return {"status": "idle", "current_status": None}
    return {
        "task_id": task["id"],
        "status": task["status"],
        "current_status": task.get("current_status"),
    }
