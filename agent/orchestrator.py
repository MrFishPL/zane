"""Orchestrator stub -- will be implemented in Task 9."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from llm_client import LLMClient
from mcp_router import MCPRouter
from models import AgentResult, OrchestratorState
from state import StateManager


class Orchestrator:
    """Phase-based orchestrator that coordinates sub-agents.

    This is a minimal stub. Full implementation comes in Task 9.
    """

    def __init__(
        self,
        llm: LLMClient,
        router: MCPRouter,
        state_mgr: StateManager | None,
        publish: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._llm = llm
        self._router = router
        self._state_mgr = state_mgr
        self._publish = publish

    async def run(
        self,
        task_id: str,
        conversation_id: str,
        user_id: str,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        raise NotImplementedError("Orchestrator.run() not yet implemented (Task 9)")

    async def resume(
        self,
        state: OrchestratorState,
        user_decisions: dict[str, str],
    ) -> AgentResult:
        raise NotImplementedError("Orchestrator.resume() not yet implemented (Task 9)")
