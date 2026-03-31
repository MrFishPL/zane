"""Tests for message creation and task submission."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from tests.conftest import (
    MOCK_AGENT_TASK,
    MOCK_CONVERSATION_ID,
    MOCK_MESSAGE,
    MOCK_MESSAGE_ID,
    MOCK_TASK_ID,
    MOCK_USER_ID,
)


class TestSendMessage:
    def test_send_message_success(self, client, mock_supabase, mock_redis):
        with (
            patch("services.supabase_client.get_agent_task", return_value=None),
            patch("services.supabase_client.create_message", return_value=MOCK_MESSAGE),
            patch("services.supabase_client.get_messages", return_value=[MOCK_MESSAGE]),
            patch("services.task_manager.submit_task", new_callable=AsyncMock, return_value=MOCK_AGENT_TASK),
            patch("routers.messages._generate_title", new_callable=AsyncMock),
        ):
            response = client.post(
                f"/api/conversations/{MOCK_CONVERSATION_ID}/messages",
                json={"content": "Find me a 10uF capacitor"},
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "accepted"
            assert data["message"]["id"] == MOCK_MESSAGE_ID
            assert data["task_id"] == MOCK_TASK_ID

    def test_send_message_conflict_when_agent_running(self, client, mock_supabase, mock_redis):
        with patch("services.supabase_client.get_agent_task", return_value=MOCK_AGENT_TASK):
            response = client.post(
                f"/api/conversations/{MOCK_CONVERSATION_ID}/messages",
                json={"content": "Another message"},
            )
            assert response.status_code == 409
            assert "already processing" in response.json()["detail"]

    def test_send_message_with_attachments(self, client, mock_supabase, mock_redis):
        message_with_att = {
            **MOCK_MESSAGE,
            "attachments": [{"path": "minio://uploads/test.pdf", "name": "test.pdf"}],
        }
        with (
            patch("services.supabase_client.get_agent_task", return_value=None),
            patch("services.supabase_client.create_message", return_value=message_with_att),
            patch("services.supabase_client.get_messages", return_value=[message_with_att]),
            patch("services.task_manager.submit_task", new_callable=AsyncMock, return_value=MOCK_AGENT_TASK),
            patch("routers.messages._generate_title", new_callable=AsyncMock),
        ):
            response = client.post(
                f"/api/conversations/{MOCK_CONVERSATION_ID}/messages",
                json={
                    "content": "Check this schematic",
                    "attachments": [{"path": "minio://uploads/test.pdf", "name": "test.pdf"}],
                },
            )
            assert response.status_code == 202

    def test_send_message_moves_staging_files(self, client, mock_supabase, mock_redis, mock_minio):
        with (
            patch("services.supabase_client.get_agent_task", return_value=None),
            patch("services.supabase_client.create_message", return_value=MOCK_MESSAGE),
            patch("services.supabase_client.get_messages", return_value=[MOCK_MESSAGE]),
            patch("services.task_manager.submit_task", new_callable=AsyncMock, return_value=MOCK_AGENT_TASK),
            patch("services.minio_client.move_files", return_value=["minio://uploads/moved.pdf"]),
            patch("routers.messages._generate_title", new_callable=AsyncMock),
        ):
            response = client.post(
                f"/api/conversations/{MOCK_CONVERSATION_ID}/messages",
                json={
                    "content": "Process this",
                    "upload_ids": ["upload-001"],
                },
            )
            assert response.status_code == 202
