"""Tests for conversation CRUD endpoints."""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import (
    MOCK_CONVERSATION,
    MOCK_CONVERSATION_ID,
    MOCK_CONVERSATION_WITH_MESSAGES,
    MOCK_AGENT_TASK,
    MOCK_USER_ID,
)


class TestCreateConversation:
    def test_create_conversation_no_title(self, client, mock_supabase):
        with patch("services.supabase_client.create_conversation", return_value=MOCK_CONVERSATION):
            response = client.post("/api/conversations", json={})
            assert response.status_code == 201
            data = response.json()
            assert data["id"] == MOCK_CONVERSATION_ID
            assert data["user_id"] == MOCK_USER_ID

    def test_create_conversation_with_title(self, client, mock_supabase):
        conv = {**MOCK_CONVERSATION, "title": "Test Conv"}
        with patch("services.supabase_client.create_conversation", return_value=conv):
            response = client.post("/api/conversations", json={"title": "Test Conv"})
            assert response.status_code == 201
            assert response.json()["title"] == "Test Conv"


class TestListConversations:
    def test_list_conversations(self, client, mock_supabase):
        convs = [
            {**MOCK_CONVERSATION, "agent_status": None, "agent_current_status": None},
        ]
        with patch("services.supabase_client.get_conversations", return_value=convs):
            response = client.get("/api/conversations")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 1

    def test_list_conversations_empty(self, client, mock_supabase):
        with patch("services.supabase_client.get_conversations", return_value=[]):
            response = client.get("/api/conversations")
            assert response.status_code == 200
            assert response.json() == []


class TestGetConversation:
    def test_get_conversation(self, client, mock_supabase):
        with patch(
            "services.supabase_client.get_conversation",
            return_value=MOCK_CONVERSATION_WITH_MESSAGES,
        ):
            response = client.get(f"/api/conversations/{MOCK_CONVERSATION_ID}")
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == MOCK_CONVERSATION_ID
            assert len(data["messages"]) == 1

    def test_get_conversation_not_found(self, client, mock_supabase):
        with patch(
            "services.supabase_client.get_conversation",
            side_effect=Exception("not found"),
        ):
            response = client.get("/api/conversations/nonexistent")
            assert response.status_code == 404


class TestUpdateConversation:
    def test_update_conversation(self, client, mock_supabase):
        updated = {**MOCK_CONVERSATION, "title": "New Title"}
        with patch("services.supabase_client.update_conversation", return_value=updated):
            response = client.patch(
                f"/api/conversations/{MOCK_CONVERSATION_ID}",
                json={"title": "New Title"},
            )
            assert response.status_code == 200
            assert response.json()["title"] == "New Title"

    def test_update_conversation_missing_title(self, client, mock_supabase):
        response = client.patch(f"/api/conversations/{MOCK_CONVERSATION_ID}", json={})
        assert response.status_code == 422  # Validation error


class TestDeleteConversation:
    def test_delete_conversation(self, client, mock_supabase, mock_minio):
        with patch("services.supabase_client.delete_conversation") as mock_del:
            with patch("services.minio_client.delete_prefix"):
                response = client.delete(f"/api/conversations/{MOCK_CONVERSATION_ID}")
                assert response.status_code == 204
                mock_del.assert_called_once_with(MOCK_CONVERSATION_ID)

    def test_delete_conversation_not_found(self, client, mock_supabase, mock_minio):
        with patch(
            "services.supabase_client.delete_conversation",
            side_effect=Exception("not found"),
        ):
            with patch("services.minio_client.delete_prefix"):
                response = client.delete("/api/conversations/nonexistent")
                assert response.status_code == 404


class TestAgentStatus:
    def test_agent_status_running(self, client, mock_supabase):
        with patch("services.supabase_client.get_agent_task", return_value=MOCK_AGENT_TASK):
            response = client.get(f"/api/conversations/{MOCK_CONVERSATION_ID}/agent-status")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "running"
            assert data["current_status"] == "searching components"

    def test_agent_status_idle(self, client, mock_supabase):
        with patch("services.supabase_client.get_agent_task", return_value=None):
            response = client.get(f"/api/conversations/{MOCK_CONVERSATION_ID}/agent-status")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "idle"
