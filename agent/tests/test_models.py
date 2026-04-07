"""Tests for agent/models.py — serialization, status properties, decisions."""

from __future__ import annotations

import json

import pytest

from models import (
    AgentResult,
    BOMEntry,
    ComponentSpec,
    Decision,
    DecisionOption,
    OrchestratorState,
    SearchResult,
)


# ---------------------------------------------------------------------------
# ComponentSpec
# ---------------------------------------------------------------------------


class TestComponentSpec:
    def test_basic_creation(self):
        c = ComponentSpec(ref="R1", type="resistor", value="8.2k")
        assert c.ref == "R1"
        assert c.type == "resistor"
        assert c.value == "8.2k"
        assert c.quantity_per_unit == 1

    def test_defaults(self):
        c = ComponentSpec(ref="C1", type="capacitor")
        assert c.description == ""
        assert c.value == ""
        assert c.package == ""
        assert c.tolerance == ""
        assert c.constraints == {}
        assert c.quantity_per_unit == 1

    def test_serialization_roundtrip(self):
        c = ComponentSpec(
            ref="U1",
            type="IC",
            description="Voltage regulator",
            value="LM317T",
            package="TO-220",
            tolerance="",
            constraints={"voltage_input": "35V"},
            quantity_per_unit=2,
        )
        data = c.model_dump()
        c2 = ComponentSpec(**data)
        assert c2 == c

    def test_json_roundtrip(self):
        c = ComponentSpec(ref="R1, R4", type="resistor", value="8.2k", quantity_per_unit=2)
        json_str = c.model_dump_json()
        c2 = ComponentSpec.model_validate_json(json_str)
        assert c2 == c


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_found_status(self):
        sr = SearchResult(
            status="found",
            ref="R1",
            mpn="RC0603FR-078K2L",
            manufacturer="Yageo",
            unit_price=0.01,
            currency="USD",
            total_stock=50000,
        )
        assert sr.is_found is True

    def test_not_found_status(self):
        sr = SearchResult(
            status="not_found",
            ref="X1",
            reason="No results after 5 queries",
        )
        assert sr.is_found is False

    def test_error_status(self):
        sr = SearchResult(status="error", ref="R1", reason="API timeout")
        assert sr.is_found is False

    def test_serialization_roundtrip(self):
        sr = SearchResult(
            status="found",
            ref="C5",
            mpn="GRM188R71H104KA93D",
            manufacturer="Murata",
            description="100nF 50V X7R 0603",
            unit_price=0.005,
            currency="USD",
            total_stock=250000,
            distributor="Digi-Key",
            distributor_stock=45000,
            distributor_url="https://www.digikey.com/product/123",
            median_price_1000=0.003,
            constraints_reasoning="Matches 100nF X7R 50V in 0603",
        )
        data = sr.model_dump()
        sr2 = SearchResult(**data)
        assert sr2.status == sr.status
        assert sr2.mpn == sr.mpn
        assert sr2.unit_price == sr.unit_price

    def test_is_found_property_not_serialized(self):
        """is_found is a property, not a field — it should not appear in dumps."""
        sr = SearchResult(status="found", ref="R1")
        data = sr.model_dump()
        assert "is_found" not in data


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class TestDecision:
    def test_decision_serialization(self):
        d = Decision(
            decision_id="d1",
            ref="R1",
            mpn="RC0603FR-078K2L",
            issue="No CAD model available",
            question="Keep original or switch to alternative with CAD?",
            options=[
                DecisionOption(key="keep", label="Keep original (no CAD)"),
                DecisionOption(
                    key="switch",
                    label="Switch to alternative",
                    mpn="CRCW06038K20FKEA",
                ),
            ],
        )
        data = d.model_dump()
        d2 = Decision(**data)
        assert d2.decision_id == "d1"
        assert len(d2.options) == 2
        assert d2.options[1].mpn == "CRCW06038K20FKEA"
        assert d2.resolved is False
        assert d2.chosen is None

    def test_decision_json_roundtrip(self):
        d = Decision(
            decision_id="d2",
            ref="C1",
            issue="Low stock",
            question="Accept MOQ of 1000?",
            options=[
                DecisionOption(key="yes", label="Accept MOQ"),
                DecisionOption(key="no", label="Find alternative"),
            ],
            resolved=True,
            chosen="yes",
        )
        json_str = d.model_dump_json()
        d2 = Decision.model_validate_json(json_str)
        assert d2 == d


# ---------------------------------------------------------------------------
# BOMEntry
# ---------------------------------------------------------------------------


class TestBOMEntry:
    def test_bom_entry_composition(self):
        comp = ComponentSpec(ref="R1", type="resistor", value="10k")
        sr = SearchResult(status="found", ref="R1", mpn="RC0603FR-0710KL")
        entry = BOMEntry(
            ref="R1", component=comp, search_result=sr, quantity_total=5
        )
        assert entry.component.value == "10k"
        assert entry.search_result.is_found is True
        assert entry.quantity_total == 5


# ---------------------------------------------------------------------------
# OrchestratorState
# ---------------------------------------------------------------------------


class TestOrchestratorState:
    def test_minimal_state(self):
        state = OrchestratorState(
            task_id="t1",
            conversation_id="c1",
            user_id="u1",
            phase="init",
        )
        assert state.production_volume == 1
        assert state.priority == "price"
        assert state.components == []
        assert state.search_results == []

    def test_full_state_roundtrip(self):
        state = OrchestratorState(
            task_id="t1",
            conversation_id="c1",
            user_id="u1",
            phase="search",
            message="Build me an audio mixer",
            production_volume=10,
            priority="availability",
            context="audio project",
            components=[
                ComponentSpec(ref="R1", type="resistor", value="8.2k"),
            ],
            search_results=[
                SearchResult(status="found", ref="R1", mpn="RC0603FR-078K2L"),
            ],
            decisions=[],
            export_files=["exports/bom.csv"],
        )
        json_str = state.model_dump_json()
        state2 = OrchestratorState.model_validate_json(json_str)
        assert state2.task_id == "t1"
        assert len(state2.components) == 1
        assert state2.components[0].value == "8.2k"
        assert state2.search_results[0].is_found is True


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


class TestAgentResult:
    def test_recommendation_result(self):
        result = AgentResult(
            status="recommendation",
            message="Found all components",
            task_id="t1",
            data={"components": [], "bom_summary": {}},
        )
        assert result.status == "recommendation"
        assert result.data is not None

    def test_needs_clarification_result(self):
        decisions = [
            Decision(
                decision_id="d1",
                ref="R1",
                issue="ambiguous",
                question="Which value?",
                options=[
                    DecisionOption(key="a", label="8.2k"),
                    DecisionOption(key="b", label="10k"),
                ],
            )
        ]
        result = AgentResult(
            status="needs_clarification",
            message="Need clarification",
            decisions=decisions,
        )
        assert len(result.decisions) == 1
        assert result.decisions[0].decision_id == "d1"
