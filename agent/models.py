"""Pydantic models for the agent orchestrator."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ComponentSpec(BaseModel):
    """A component identified from schematic analysis."""
    ref: str
    type: str
    description: str = ""
    value: str = ""
    package: str = ""
    tolerance: str = ""
    constraints: dict[str, object] = Field(default_factory=dict)
    quantity_per_unit: int = 1


class SearchResult(BaseModel):
    """Result from a component search sub-agent."""
    status: str  # "found", "not_found", "error"
    ref: str
    mpn: str | None = None
    manufacturer: str | None = None
    description: str | None = None
    unit_price: float | None = None
    currency: str | None = None
    total_stock: int | None = None
    distributor: str | None = None
    distributor_stock: int | None = None
    distributor_url: str | None = None
    octopart_url: str | None = None
    median_price_1000: dict | None = None
    constraints_reasoning: str | None = None
    reason: str | None = None

    @property
    def is_found(self) -> bool:
        return self.status == "found"


class DecisionOption(BaseModel):
    key: str
    label: str
    mpn: str | None = None


class Decision(BaseModel):
    decision_id: str
    ref: str
    mpn: str | None = None
    issue: str
    question: str
    options: list[DecisionOption]
    resolved: bool = False
    chosen: str | None = None


class BOMEntry(BaseModel):
    ref: str
    component: ComponentSpec
    search_result: SearchResult
    quantity_total: int = 0


class OrchestratorState(BaseModel):
    task_id: str
    conversation_id: str
    user_id: str
    phase: str
    message: str = ""
    production_volume: int = 1
    priority: str = "price"
    context: str = ""
    components: list[ComponentSpec] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    export_files: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    status: str
    message: str
    task_id: str | None = None
    data: dict | None = None
    decisions: list[Decision] | None = None
