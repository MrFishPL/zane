"""Supabase client wrapper for conversations, messages, and agent tasks."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import structlog
from supabase import Client, create_client

log = structlog.get_logger()

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        raise RuntimeError("Supabase client not initialised — call init() first")
    return _client


def init() -> Client:
    """Initialise the Supabase client from environment variables."""
    global _client
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    _client = create_client(url, key)
    log.info("supabase.initialised", url=url)
    return _client


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def create_conversation(user_id: str = DEFAULT_USER_ID, title: str | None = None) -> dict:
    """Create a new conversation and return the row."""
    data = {"user_id": user_id}
    if title:
        data["title"] = title
    result = (
        get_client()
        .table("conversations")
        .insert(data)
        .execute()
    )
    row = result.data[0]
    log.info("supabase.conversation.created", conversation_id=row["id"])
    return row


def get_conversations(user_id: str = DEFAULT_USER_ID) -> list[dict]:
    """List conversations for a user, including the latest agent_task status."""
    result = (
        get_client()
        .table("conversations")
        .select("*, agent_tasks(id, status, current_status, created_at)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    conversations = result.data
    # Flatten: pick latest agent_task per conversation
    for conv in conversations:
        tasks = conv.pop("agent_tasks", []) or []
        if tasks:
            latest = max(tasks, key=lambda t: t["created_at"])
            conv["agent_status"] = latest["status"]
            conv["agent_current_status"] = latest.get("current_status")
        else:
            conv["agent_status"] = None
            conv["agent_current_status"] = None
    log.info("supabase.conversations.listed", user_id=user_id, count=len(conversations))
    return conversations


def get_conversation(conversation_id: str) -> dict:
    """Get a single conversation with its messages."""
    result = (
        get_client()
        .table("conversations")
        .select("*, messages(*)")
        .eq("id", conversation_id)
        .single()
        .execute()
    )
    conv = result.data
    # Sort messages by created_at ascending
    if conv.get("messages"):
        conv["messages"].sort(key=lambda m: m["created_at"])
    log.info("supabase.conversation.fetched", conversation_id=conversation_id)
    return conv


def update_conversation(conversation_id: str, title: str) -> dict:
    """Update conversation title."""
    result = (
        get_client()
        .table("conversations")
        .update({"title": title})
        .eq("id", conversation_id)
        .execute()
    )
    row = result.data[0]
    log.info("supabase.conversation.updated", conversation_id=conversation_id, title=title)
    return row


def delete_conversation(conversation_id: str) -> None:
    """Delete a conversation — cascading deletes handle messages and agent_tasks."""
    get_client().table("conversations").delete().eq("id", conversation_id).execute()
    log.info("supabase.conversation.deleted", conversation_id=conversation_id)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def create_message(
    conversation_id: str,
    role: str,
    content: str,
    attachments: list[dict] | None = None,
) -> dict:
    """Create a message in a conversation."""
    data = {
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
    }
    if attachments:
        data["attachments"] = attachments
    result = (
        get_client()
        .table("messages")
        .insert(data)
        .execute()
    )
    row = result.data[0]
    log.info(
        "supabase.message.created",
        conversation_id=conversation_id,
        message_id=row["id"],
        role=role,
    )
    return row


def get_messages(conversation_id: str) -> list[dict]:
    """Get all messages for a conversation, ordered by creation time."""
    result = (
        get_client()
        .table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    log.info(
        "supabase.messages.listed",
        conversation_id=conversation_id,
        count=len(result.data),
    )
    return result.data


# ---------------------------------------------------------------------------
# Agent tasks
# ---------------------------------------------------------------------------

def create_agent_task(conversation_id: str, message_id: str) -> dict:
    """Create an agent_task row with status 'running'."""
    data = {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "status": "running",
    }
    result = (
        get_client()
        .table("agent_tasks")
        .insert(data)
        .execute()
    )
    row = result.data[0]
    log.info(
        "supabase.agent_task.created",
        task_id=row["id"],
        conversation_id=conversation_id,
    )
    return row


def update_agent_task(
    task_id: str,
    *,
    status: str | None = None,
    current_status: str | None = None,
    error: str | None = None,
    completed_at: str | None = None,
) -> dict:
    """Update fields on an agent_task."""
    updates: dict = {}
    if status is not None:
        updates["status"] = status
    if current_status is not None:
        updates["current_status"] = current_status
    if error is not None:
        updates["error"] = error
    if completed_at is not None:
        updates["completed_at"] = completed_at
    if not updates:
        raise ValueError("No fields to update")
    result = (
        get_client()
        .table("agent_tasks")
        .update(updates)
        .eq("id", task_id)
        .execute()
    )
    row = result.data[0]
    log.info("supabase.agent_task.updated", task_id=task_id, updates=updates)
    return row


def get_agent_task(conversation_id: str) -> dict | None:
    """Get the latest running agent_task for a conversation, or None."""
    result = (
        get_client()
        .table("agent_tasks")
        .select("*")
        .eq("conversation_id", conversation_id)
        .eq("status", "running")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None
