"""Pytest fixtures — mock Supabase, MinIO, Redis, and provide a FastAPI test client."""

from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure the backend root is on sys.path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Mock service responses
# ---------------------------------------------------------------------------

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"
MOCK_CONVERSATION_ID = "conv-001"
MOCK_MESSAGE_ID = "msg-001"
MOCK_TASK_ID = "task-001"

MOCK_CONVERSATION = {
    "id": MOCK_CONVERSATION_ID,
    "user_id": MOCK_USER_ID,
    "title": None,
    "created_at": "2026-03-31T10:00:00+00:00",
    "updated_at": "2026-03-31T10:00:00+00:00",
}

MOCK_CONVERSATION_WITH_MESSAGES = {
    **MOCK_CONVERSATION,
    "messages": [
        {
            "id": MOCK_MESSAGE_ID,
            "conversation_id": MOCK_CONVERSATION_ID,
            "role": "user",
            "content": "Hello",
            "attachments": None,
            "created_at": "2026-03-31T10:00:01+00:00",
        }
    ],
}

MOCK_MESSAGE = {
    "id": MOCK_MESSAGE_ID,
    "conversation_id": MOCK_CONVERSATION_ID,
    "role": "user",
    "content": "Hello",
    "attachments": None,
    "created_at": "2026-03-31T10:00:01+00:00",
}

MOCK_AGENT_TASK = {
    "id": MOCK_TASK_ID,
    "conversation_id": MOCK_CONVERSATION_ID,
    "message_id": MOCK_MESSAGE_ID,
    "status": "running",
    "current_status": "searching components",
    "error": None,
    "created_at": "2026-03-31T10:00:02+00:00",
    "completed_at": None,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_supabase():
    """Patch the supabase_client module with mock functions."""
    mock_client = MagicMock()
    with patch("services.supabase_client._client", new=mock_client):
        with patch("services.supabase_client.get_client", return_value=mock_client):
            yield mock_client


@pytest.fixture()
def mock_minio():
    """Patch the minio_client module with mock functions."""
    mock_client = MagicMock()
    with patch("services.minio_client._client", new=mock_client):
        with patch("services.minio_client.get_client", return_value=mock_client):
            yield mock_client


@pytest.fixture()
def mock_redis():
    """Patch the redis_client module with mock async functions."""
    mock_client = AsyncMock()
    mock_client.ping = AsyncMock()
    mock_client.lpush = AsyncMock()
    mock_client.llen = AsyncMock(return_value=0)
    with patch("services.redis_client._client", new=mock_client):
        with patch("services.redis_client.get_client", return_value=mock_client):
            yield mock_client


@pytest.fixture()
def client(mock_supabase, mock_minio, mock_redis):
    """Create a FastAPI TestClient with all services mocked.

    We patch the lifespan init/close functions so the app starts without
    real infrastructure.
    """
    with (
        patch("services.supabase_client.init", return_value=mock_supabase),
        patch("services.minio_client.init", return_value=mock_minio),
        patch("services.minio_client.cleanup_staging", return_value=0),
        patch("services.redis_client.init", new_callable=AsyncMock, return_value=mock_redis),
        patch("services.redis_client.close", new_callable=AsyncMock),
    ):
        from main import app
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc
