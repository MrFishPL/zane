# Agent Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic agent loop with an orchestrator + parallel sub-agent architecture, fix the Nexar API integration, add interactive user decisions, and remove the LiteLLM proxy.

**Architecture:** Phase-based orchestrator dispatches parallel search sub-agents. Pause/resume via Redis state serialization. Direct OpenAI API calls (no LiteLLM proxy). Fixed Nexar GraphQL queries based on live API testing.

**Tech Stack:** Python 3.12, AsyncIO, openai SDK, Redis, Pydantic, FastMCP, Next.js 16, React 19, TypeScript

**Spec:** `docs/superpowers/specs/2026-04-01-agent-rewrite-design.md`

---

## Task Dependency Graph

```
Task 1 (mcp-nexar fix) ─────────────────────────────────┐
Task 2 (mcp-snapmagic simplify) ─────────────────────────┤
Task 3 (mcp-websearch fix) ──────────────────────────────┤
Task 4 (agent models.py) ───────────────────────────────┐├─→ Task 9 (orchestrator)
Task 5 (agent llm_client.py) ───────────────────────────┤├─→ Task 10 (worker rewrite)
Task 6 (agent prompts/) ────────────────────────────────┤├─→ Task 14 (integration test)
Task 7 (agent state.py) ────────────────────────────────┤│
Task 8 (agent search_agent.py) ─────────────────────────┘│
Task 11 (backend decision routing) ──────────────────────┤
Task 12 (frontend decision cards) ───────────────────────┘
Task 13 (docker/infra cleanup) ── do after 1-12
Task 15 (build & smoke test) ── do last
```

Tasks 1-8 can run in parallel. Tasks 9-10 depend on 1-8. Tasks 11-12 can run in parallel with 9-10.

---

### Task 1: Fix mcp-nexar GraphQL Queries

**Files:**
- Modify: `mcp-nexar/nexar_client.py`
- Modify: `mcp-nexar/server.py`
- Modify: `mcp-nexar/tests/test_tools.py`

- [ ] **Step 1: Write tests for new query behavior**

Create tests in `mcp-nexar/tests/test_tools.py` that verify:

```python
# Add to existing test file

@pytest.mark.asyncio
async def test_search_parts_returns_totalAvail(mock_client):
    """search_parts must return totalAvail, octopartUrl, category."""
    result = await mock_client.search_parts("10k resistor 0603")
    assert "results" in result
    part = result["results"][0]
    assert "total_avail" in part
    assert "octopart_url" in part
    assert "category" in part
    # These fields should NOT exist (unauthorized on current plan)
    assert "specs" not in part
    assert "datasheet_url" not in part


@pytest.mark.asyncio
async def test_search_mpn_uses_supSearchMpn(mock_client, httpx_mock):
    """search_mpn must use supSearchMpn, not supSearch."""
    # The mock should verify the query string contains "supSearchMpn"
    result = await mock_client.search_mpn("OPA1612AIDR")
    assert result["results"][0]["mpn"] == "OPA1612AIDR"


@pytest.mark.asyncio
async def test_multi_match_uses_native_query(mock_client, httpx_mock):
    """multi_match must use native supMultiMatch, not loop of search_mpn."""
    result = await mock_client.multi_match(["OPA1612AIDR", "LM386N-1"])
    assert "OPA1612AIDR" in result["results"]
    assert "LM386N-1" in result["results"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mcp-nexar && python -m pytest tests/test_tools.py -v -k "totalAvail or supSearchMpn or native_query"`
Expected: FAIL

- [ ] **Step 3: Replace SEARCH_QUERY in nexar_client.py**

Replace the entire `SEARCH_QUERY` constant (lines 15-38) and add new queries:

```python
SEARCH_QUERY = """
query SearchParts($query: String!, $limit: Int!, $country: String, $currency: String) {
  supSearch(q: $query, limit: $limit, country: $country, currency: $currency) {
    hits
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        medianPrice1000 { price currency }
        totalAvail
        octopartUrl
        category { name }
        sellers(authorizedOnly: true) {
          company { name }
          offers {
            inventoryLevel
            moq
            sku
            prices { quantity price currency convertedPrice convertedCurrency }
            clickUrl
          }
        }
      }
    }
  }
}
"""

SEARCH_MPN_QUERY = """
query SearchMpn($query: String!, $limit: Int!, $country: String, $currency: String) {
  supSearchMpn(q: $query, limit: $limit, country: $country, currency: $currency) {
    hits
    results {
      part {
        mpn
        manufacturer { name }
        shortDescription
        medianPrice1000 { price currency }
        totalAvail
        octopartUrl
        category { name }
        sellers(authorizedOnly: true) {
          company { name }
          offers {
            inventoryLevel
            moq
            sku
            prices { quantity price currency convertedPrice convertedCurrency }
            clickUrl
          }
        }
      }
    }
  }
}
"""

MULTI_MATCH_QUERY = """
query MultiMatch($queries: [SupPartMatchQuery!]!, $country: String, $currency: String) {
  supMultiMatch(queries: $queries, country: $country, currency: $currency) {
    hits
    parts {
      mpn
      manufacturer { name }
      shortDescription
      medianPrice1000 { price currency }
      totalAvail
      octopartUrl
      category { name }
      sellers(authorizedOnly: true) {
        company { name }
        offers {
          inventoryLevel
          moq
          sku
          prices { quantity price currency convertedPrice convertedCurrency }
          clickUrl
        }
      }
    }
  }
}
"""
```

- [ ] **Step 4: Remove KEY_SPECS and rewrite _compress_part()**

Delete the `KEY_SPECS` set (lines 42-56). Replace `_compress_part()` (lines 103-166):

```python
def _compress_part(self, part: dict[str, Any]) -> dict[str, Any]:
    """Compress a part result: top 5 sellers, max 3 price breaks."""
    if not part:
        return {}

    sellers = []
    for seller in (part.get("sellers") or [])[:5]:
        compressed_offers = []
        for offer in (seller.get("offers") or [])[:3]:
            compressed_offers.append({
                "stock": offer.get("inventoryLevel"),
                "moq": offer.get("moq"),
                "sku": offer.get("sku"),
                "prices": (offer.get("prices") or [])[:3],
                "url": offer.get("clickUrl"),
            })
        sellers.append({
            "name": seller.get("company", {}).get("name", ""),
            "offers": compressed_offers,
        })

    return {
        "mpn": part.get("mpn"),
        "manufacturer": (part.get("manufacturer") or {}).get("name"),
        "description": part.get("shortDescription"),
        "category": (part.get("category") or {}).get("name"),
        "total_avail": part.get("totalAvail", 0),
        "octopart_url": part.get("octopartUrl"),
        "median_price_1000": part.get("medianPrice1000"),
        "sellers": sellers,
    }
```

- [ ] **Step 5: Update search_mpn() to use supSearchMpn**

Replace `search_mpn()` method (lines 207-228):

```python
async def search_mpn(self, mpn: str) -> dict[str, Any]:
    """Search for a component by exact MPN using supSearchMpn."""
    start = time.monotonic()
    log.info("nexar_client.search_mpn", mpn=mpn[:200])
    try:
        data = await self._execute_query(
            SEARCH_MPN_QUERY,
            {"query": mpn, "limit": 3, "country": self._country, "currency": self._currency},
        )
        result = self._compress_results(data, root_key="supSearchMpn")
        duration_ms = round((time.monotonic() - start) * 1000)
        log.info("nexar_client.search_mpn.ok", hits=result["hits"], duration_ms=duration_ms)
        return result
    except Exception:
        duration_ms = round((time.monotonic() - start) * 1000)
        log.error("nexar_client.search_mpn.error", duration_ms=duration_ms)
        raise
```

- [ ] **Step 6: Rewrite multi_match() to use native supMultiMatch**

Replace `multi_match()` method (lines 230-254):

```python
async def multi_match(self, mpns: list[str]) -> dict[str, Any]:
    """Batch lookup of multiple MPNs using native supMultiMatch."""
    start = time.monotonic()
    log.info("nexar_client.multi_match", count=len(mpns))
    try:
        queries = [{"mpn": mpn} for mpn in mpns]
        data = await self._execute_query(
            MULTI_MATCH_QUERY,
            {"queries": queries, "country": self._country, "currency": self._currency},
        )
        raw_results = data.get("supMultiMatch") or []
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for i, match_result in enumerate(raw_results):
            mpn = mpns[i] if i < len(mpns) else f"unknown_{i}"
            parts = match_result.get("parts") or []
            if parts:
                results[mpn] = {
                    "hits": match_result.get("hits", 0),
                    "results": [self._compress_part(p) for p in parts],
                }
            else:
                errors[mpn] = "No results found"
        duration_ms = round((time.monotonic() - start) * 1000)
        log.info("nexar_client.multi_match.ok", total=len(mpns), found=len(results), duration_ms=duration_ms)
        return {"results": results, "errors": errors}
    except Exception:
        duration_ms = round((time.monotonic() - start) * 1000)
        log.error("nexar_client.multi_match.error", duration_ms=duration_ms)
        raise
```

- [ ] **Step 7: Add country/currency to __init__ and search_parts**

First, add `import os` at the top of `nexar_client.py` (after `import time`).

Add to `__init__`:
```python
def __init__(self, client_id: str, client_secret: str,
             country: str | None = None, currency: str | None = None) -> None:
    self._auth = NexarAuth(client_id, client_secret)
    self._country = country or os.environ.get("NEXAR_COUNTRY")
    self._currency = currency or os.environ.get("NEXAR_CURRENCY")
```

Update `search_parts()` variables to include country/currency:
```python
data = await self._execute_query(
    SEARCH_QUERY,
    {"query": query, "limit": 5, "country": self._country, "currency": self._currency},
)
```

Make `_compress_results()` accept a `root_key` parameter:
```python
def _compress_results(self, data: dict[str, Any], root_key: str = "supSearch") -> dict[str, Any]:
    sup_search = data.get(root_key, {})
    # ... rest unchanged
```

- [ ] **Step 8: Remove check_lifecycle from server.py and client**

In `nexar_client.py`: delete `check_lifecycle()` method (lines 256-291).

In `server.py`: delete the `check_lifecycle` tool registration and its handler. Remove the `get_quota_status` tool (it's a placeholder that returns nothing useful).

Keep only 3 tools: `search_parts`, `search_mpn`, `multi_match`.

- [ ] **Step 9: Update test fixtures for new response shape**

Update mock responses in `tests/test_tools.py` and `tests/conftest.py` to match the new query fields (`totalAvail`, `octopartUrl`, `category`, no `specs`/`bestDatasheet`). Ensure all existing tests still pass with the new shape.

- [ ] **Step 10: Run all mcp-nexar tests**

Run: `cd mcp-nexar && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 11: Commit**

```bash
git add mcp-nexar/
git commit -m "fix(mcp-nexar): use supSearchMpn, native supMultiMatch, remove unauthorized fields"
```

---

### Task 2: Simplify mcp-snapmagic

**Files:**
- Modify: `mcp-snapmagic/search_client.py`
- Modify: `mcp-snapmagic/server.py`
- Modify: `mcp-snapmagic/tests/test_tools.py`

- [ ] **Step 1: Write test for parallelized check_cad_batch**

```python
@pytest.mark.asyncio
async def test_check_cad_batch_parallel(mock_client):
    """check_cad_batch should process MPNs concurrently."""
    result = await mock_client.check_batch(["OPA1612AIDR", "LM386N-1", "RC0603FR-0710KL"])
    assert len(result) == 3
    # All should have 'available' key
    for mpn, status in result.items():
        assert "available" in status
```

- [ ] **Step 2: Run test to verify it fails (or passes with current sequential impl)**

Run: `cd mcp-snapmagic && python -m pytest tests/test_tools.py -v -k "batch_parallel"`

- [ ] **Step 3: Parallelize check_batch in search_client.py**

Replace the sequential loop in `check_batch()` with `asyncio.gather()`:

```python
async def check_batch(self, mpns: list[str], format: str = "any") -> dict[str, Any]:
    """Check CAD availability for multiple MPNs concurrently."""
    async def _check_one(mpn: str) -> tuple[str, dict]:
        result = await self.check_availability(mpn, format)
        return mpn, result

    tasks = [_check_one(mpn) for mpn in mpns]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    results = {}
    for item in results_list:
        if isinstance(item, Exception):
            continue
        mpn, result = item
        results[mpn] = result
    return results
```

- [ ] **Step 4: Update server.py — return dict-by-MPN from check_cad_batch**

The orchestrator expects `check_cad_batch` to return `{mpn: {available, url, formats}}`. The current server.py returns `{"results": [list]}`. Update the `check_cad_batch` tool handler in `server.py` to return a dict keyed by MPN:

```python
@mcp.tool()
async def check_cad_batch(mpns: list[str], format: str = "any") -> dict:
    """Check CAD model availability for multiple MPNs."""
    results = await search_client.check_batch(mpns, format)
    return results  # Already a dict keyed by MPN from the updated check_batch
```

Also remove any unused `LITELLM_BASE_URL` references from server.py.

- [ ] **Step 5: Run all tests**

Run: `cd mcp-snapmagic && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add mcp-snapmagic/
git commit -m "fix(mcp-snapmagic): parallelize batch CAD check, remove unused LITELLM_BASE_URL"
```

---

### Task 3: Fix mcp-websearch to use OpenAI directly

**Files:**
- Modify: `mcp-websearch/search_client.py`
- Modify: `mcp-websearch/server.py`
- Modify: `mcp-websearch/tests/test_tools.py`

- [ ] **Step 1: Write test for direct OpenAI web search**

```python
@pytest.mark.asyncio
async def test_search_uses_openai_directly(mock_httpx):
    """search_distributor should call OpenAI API directly, not LiteLLM."""
    client = SearchClient()
    result = await client.search("OPA1612AIDR", "mouser.com")
    # Verify request went to api.openai.com, not litellm-proxy
    assert mock_httpx.last_request.url.host == "api.openai.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-websearch && python -m pytest tests/test_tools.py -v -k "openai_directly"`
Expected: FAIL

- [ ] **Step 3: Update search_client.py to use OpenAI directly**

Replace the LiteLLM proxy URL with direct OpenAI API. Change `{"type": "web_search"}` to `{"type": "web_search_preview"}` (OpenAI's native web search tool). Use `OPENAI_API_KEY` env var instead of routing through LiteLLM.

Key changes in `search_client.py`:
- Replace `LITELLM_BASE_URL` with `https://api.openai.com/v1`
- Replace `web_search` tool type with `web_search_preview`
- Use `OPENAI_API_KEY` for auth header

- [ ] **Step 4: Update server.py env var references**

Remove any `LITELLM_BASE_URL` references. Add `OPENAI_API_KEY` to required env vars.

- [ ] **Step 5: Run all tests**

Run: `cd mcp-websearch && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add mcp-websearch/
git commit -m "fix(mcp-websearch): use OpenAI API directly instead of LiteLLM proxy"
```

---

### Task 4: Create agent/models.py (Pydantic Models)

**Files:**
- Create: `agent/models.py`
- Create: `agent/tests/test_models.py`

- [ ] **Step 1: Write tests for all models**

```python
# agent/tests/test_models.py
import pytest
from models import ComponentSpec, SearchResult, Decision, DecisionOption, BOMEntry, OrchestratorState, AgentResult


def test_component_spec_from_llm_output():
    data = {
        "ref": "U1", "type": "op-amp", "description": "Low-noise op-amp",
        "value": "OPA1612", "package": "SOIC-8",
        "constraints": {"noise": "<1.1 nV/rtHz"}, "quantity_per_unit": 2,
    }
    spec = ComponentSpec(**data)
    assert spec.ref == "U1"
    assert spec.quantity_per_unit == 2
    assert spec.constraints == {"noise": "<1.1 nV/rtHz"}


def test_search_result_found():
    result = SearchResult(
        status="found", ref="U1", mpn="OPA1612AIDR",
        manufacturer="Texas Instruments",
        description="Dual op-amp", unit_price=4.23, currency="USD",
        total_stock=725546, distributor="DigiKey", distributor_stock=8674,
        distributor_url="https://...", octopart_url="https://...",
    )
    assert result.status == "found"
    assert result.is_found


def test_search_result_not_found():
    result = SearchResult(status="not_found", ref="U1", reason="No match")
    assert not result.is_found


def test_decision_serialization():
    d = Decision(
        decision_id="d1", ref="U3", mpn="LM386N",
        issue="no_cad_model", question="No 3D model",
        options=[
            DecisionOption(key="A", label="Add without model"),
            DecisionOption(key="B", label="Use alternative", mpn="LM386M-1"),
        ],
    )
    data = d.model_dump()
    assert data["resolved"] is False
    assert data["chosen"] is None
    assert len(data["options"]) == 2


def test_orchestrator_state_roundtrip():
    state = OrchestratorState(
        task_id="t1", conversation_id="c1", user_id="u1",
        phase="awaiting_decision",
        components=[ComponentSpec(ref="R1", type="resistor", value="10k",
                                  package="0603", quantity_per_unit=4)],
        search_results=[SearchResult(status="found", ref="R1", mpn="RC0603FR-0710KL",
                                      manufacturer="Yageo", description="10k 0603",
                                      unit_price=0.004, currency="USD", total_stock=500000000,
                                      distributor="DigiKey", distributor_stock=100000,
                                      distributor_url="https://...", octopart_url="https://...")],
    )
    json_str = state.model_dump_json()
    restored = OrchestratorState.model_validate_json(json_str)
    assert restored.task_id == "t1"
    assert restored.phase == "awaiting_decision"
    assert len(restored.components) == 1
    assert len(restored.search_results) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_models.py -v`
Expected: FAIL (models.py doesn't exist)

- [ ] **Step 3: Create agent/models.py**

```python
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
    constraints: dict[str, str] = Field(default_factory=dict)
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
    reason: str | None = None  # For not_found/error

    @property
    def is_found(self) -> bool:
        return self.status == "found"


class DecisionOption(BaseModel):
    """One option in a user decision."""
    key: str
    label: str
    mpn: str | None = None


class Decision(BaseModel):
    """A decision the user needs to make."""
    decision_id: str
    ref: str
    mpn: str | None = None
    issue: str
    question: str
    options: list[DecisionOption]
    resolved: bool = False
    chosen: str | None = None


class CADStatus(BaseModel):
    """CAD availability status for one component."""
    mpn: str
    available: bool
    url: str | None = None
    formats: list[str] = Field(default_factory=list)


class BOMEntry(BaseModel):
    """A single entry in the final BOM."""
    ref: str
    component: ComponentSpec
    search_result: SearchResult
    cad_status: CADStatus | None = None
    quantity_total: int = 0  # quantity_per_unit * production_volume


class OrchestratorState(BaseModel):
    """Serializable state for pause/resume."""
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
    cad_statuses: list[CADStatus] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    export_files: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Final result returned by the orchestrator."""
    status: str  # "recommendation", "needs_clarification", "decision_required", "analysis"
    message: str
    task_id: str | None = None
    data: dict | None = None
    decisions: list[Decision] | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd agent && python -m pytest tests/test_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add agent/models.py agent/tests/test_models.py
git commit -m "feat(agent): add Pydantic models for orchestrator state and BOM"
```

---

### Task 5: Create agent/llm_client.py

**Files:**
- Create: `agent/llm_client.py`
- Create: `agent/tests/test_llm_client.py`

- [ ] **Step 1: Write tests**

```python
# agent/tests/test_llm_client.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from llm_client import LLMClient


@pytest.mark.asyncio
async def test_analyze_schematic_returns_json():
    """Phase 2 call should parse JSON from LLM response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"components": []}'

    with patch("llm_client.AsyncOpenAI") as MockOpenAI:
        client_instance = AsyncMock()
        client_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        MockOpenAI.return_value = client_instance

        llm = LLMClient()
        result = await llm.analyze_schematic([], [], "find components")
        assert result == {"components": []}


@pytest.mark.asyncio
async def test_chat_with_tools_returns_response():
    """Phase 3 call should return the raw OpenAI response for tool processing."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].message.content = '{"status": "found"}'
    mock_response.choices[0].finish_reason = "stop"

    with patch("llm_client.AsyncOpenAI") as MockOpenAI:
        client_instance = AsyncMock()
        client_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        MockOpenAI.return_value = client_instance

        llm = LLMClient()
        resp = await llm.chat(messages=[{"role": "user", "content": "test"}], tools=[])
        assert resp is mock_response


@pytest.mark.asyncio
async def test_retry_on_timeout():
    """Should retry with escalating timeouts."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"

    import openai
    with patch("llm_client.AsyncOpenAI") as MockOpenAI:
        client_instance = AsyncMock()
        client_instance.chat.completions.create = AsyncMock(
            side_effect=[openai.APITimeoutError(request=MagicMock()), mock_response]
        )
        MockOpenAI.return_value = client_instance

        llm = LLMClient()
        resp = await llm.chat(
            messages=[{"role": "user", "content": "test"}],
            tools=[], timeout_tiers=[1, 2],
        )
        assert resp is mock_response
        assert client_instance.chat.completions.create.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_llm_client.py -v`
Expected: FAIL

- [ ] **Step 3: Create agent/llm_client.py**

```python
"""Thin wrapper around AsyncOpenAI for direct API access."""

import json
import os
from typing import Any

import structlog
from openai import AsyncOpenAI, APITimeoutError

log = structlog.get_logger()

# Default timeout tiers (escalating per-request timeouts)
PHASE2_TIMEOUTS = [180, 360, 720]
PHASE3_TIMEOUTS = [60, 120, 240]


class LLMClient:
    """LLM client that talks directly to OpenAI API (no LiteLLM proxy)."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self._model = os.environ.get("OPENAI_MODEL", "gpt-5.4")
        self._reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "high")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        timeout_tiers: list[int] | None = None,
    ) -> Any:
        """Make a chat completion call with retry on timeout."""
        tiers = timeout_tiers or PHASE3_TIMEOUTS
        last_error = None

        for attempt, timeout in enumerate(tiers):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "timeout": timeout,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                try:
                    kwargs["reasoning_effort"] = self._reasoning_effort
                    response = await self._client.chat.completions.create(**kwargs)
                except (TypeError, Exception) as e:
                    if "reasoning_effort" in str(e):
                        kwargs.pop("reasoning_effort", None)
                        response = await self._client.chat.completions.create(**kwargs)
                    else:
                        raise
                return response
            except APITimeoutError as e:
                last_error = e
                log.warning("llm_client.timeout", attempt=attempt + 1, timeout=timeout)
                continue

        raise last_error  # type: ignore[misc]

    async def analyze_schematic(
        self,
        images_base64: list[str],
        text_blocks: list[str],
        user_message: str,
        system_prompt: str = "",
    ) -> dict[str, Any]:
        """Phase 2: Single LLM call for schematic analysis. Returns parsed JSON."""
        content_parts: list[dict] = []

        for img in images_base64:
            prefix = "" if img.startswith("data:") else "data:image/jpeg;base64,"
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"{prefix}{img}"},
            })

        if text_blocks:
            content_parts.append({
                "type": "text",
                "text": "Extracted text from schematic pages:\n\n" + "\n---\n".join(text_blocks),
            })

        content_parts.append({"type": "text", "text": user_message})

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        response = await self.chat(messages, timeout_tiers=PHASE2_TIMEOUTS)

        raw = response.choices[0].message.content or "{}"
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        return json.loads(raw)
```

- [ ] **Step 4: Run tests**

Run: `cd agent && python -m pytest tests/test_llm_client.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add agent/llm_client.py agent/tests/test_llm_client.py
git commit -m "feat(agent): add LLMClient with direct OpenAI API access and retry"
```

---

### Task 6: Create agent/prompts/

**Files:**
- Create: `agent/prompts/__init__.py`
- Create: `agent/prompts/orchestrator.py`
- Create: `agent/prompts/search_agent.py`

- [ ] **Step 1: Create agent/prompts/__init__.py**

```python
from .orchestrator import ORCHESTRATOR_SYSTEM_PROMPT
from .search_agent import SEARCH_AGENT_SYSTEM_PROMPT
```

- [ ] **Step 2: Create agent/prompts/orchestrator.py**

```python
"""System prompt for Phase 2: schematic analysis."""

ORCHESTRATOR_SYSTEM_PROMPT = """You are an expert electronic component sourcing engineer. You analyze schematics and identify all components.

## Your Task

Analyze the provided schematic images and extracted text. Identify every electronic component and produce a structured JSON list.

## Output Format

Return ONLY valid JSON (no markdown, no explanation) with this structure:

{
  "components": [
    {
      "ref": "U1",
      "type": "op-amp",
      "description": "Low-noise operational amplifier for audio input stage",
      "value": "OPA1612",
      "package": "SOIC-8",
      "tolerance": "",
      "constraints": {"noise": "<1.1 nV/rtHz", "supply": "+/-15V"},
      "quantity_per_unit": 2
    },
    {
      "ref": "R1-R4",
      "type": "resistor",
      "value": "10k",
      "package": "0603",
      "tolerance": "1%",
      "constraints": {},
      "quantity_per_unit": 4
    }
  ],
  "production_volume": 1000,
  "priority": "price",
  "context": "audio mixer, low-noise input stage"
}

## Rules

1. Examine ALL pages/images. Do not miss any component.
2. For each component, extract: reference designator, type, value, package/footprint, tolerance, quantity.
3. Group identical components (e.g., R1-R4 if all are 10k 0603 1%).
4. If a value or package is not visible, leave it empty string — do not guess.
5. Extract constraints from the user message (e.g., "low noise", "automotive grade", "dust-proof").
6. Set production_volume from user message. Default to 1 if not mentioned.
7. Set priority from user message: "price" (default), "availability", or "quality".
8. Set context from user message (application type, environment, etc.).
9. If the schematic uses generic symbols (e.g., just "R" with no value), note what you can see and leave value empty.
10. Never invent component values or specifications not visible in the schematic or mentioned by the user.
"""
```

- [ ] **Step 3: Create agent/prompts/search_agent.py**

```python
"""System prompt for Phase 3: component search sub-agent."""

SEARCH_AGENT_SYSTEM_PROMPT = """You are a component sourcing specialist. Your job is to find ONE specific electronic component that matches the given specification.

## Your Task

Search for a real, purchasable electronic component matching the spec below. Use the search tools provided.

## Search Strategy

1. Start with `search_parts` using a descriptive query (e.g., "10k ohm resistor 0603 1%").
2. Evaluate results by reading `shortDescription` — verify it matches the required type, value, package.
3. If a specific MPN is known, use `search_mpn` for exact lookup.
4. If no good match on first try, adjust keywords:
   - Try different value formats: "10kohm" vs "10k ohm" vs "10000 ohm"
   - Try adding/removing package info
   - Try manufacturer-specific terms
5. If Nexar returns nothing useful after 3 attempts, use `search_distributor` as fallback.
6. NEVER invent or hallucinate an MPN. Only return MPNs you found in search results.

## Selection Criteria

Pick the component that best matches ALL of these (in order of priority):
1. Correct value, package, and tolerance (must match)
2. Stock available (total_avail > 0, prefer > required quantity)
3. Price (prefer lowest unit price, unless user specified "availability" priority)
4. Authorized distributor (prefer authorized sellers)

## Output Format

When you find the right component, respond with ONLY this JSON (no markdown, no explanation):

{
  "status": "found",
  "ref": "<reference from spec>",
  "mpn": "<real MPN from search results>",
  "manufacturer": "<from search results>",
  "description": "<shortDescription from search results>",
  "unit_price": <number>,
  "currency": "<from search results>",
  "total_stock": <number>,
  "distributor": "<best seller name>",
  "distributor_stock": <number>,
  "distributor_url": "<clickUrl from search results>",
  "octopart_url": "<octopartUrl from search results>",
  "median_price_1000": {"price": <number>, "currency": "<code>"},
  "constraints_reasoning": "<explain how this component meets the constraints>"
}

If you cannot find a suitable component after exhausting searches:

{
  "status": "not_found",
  "ref": "<reference from spec>",
  "reason": "<explain what you searched and why nothing matched>"
}
"""
```

- [ ] **Step 4: Commit**

```bash
git add agent/prompts/
git commit -m "feat(agent): add focused prompts for orchestrator and search sub-agent"
```

---

### Task 7: Create agent/state.py (Pause/Resume State Management)

**Files:**
- Create: `agent/state.py`
- Create: `agent/tests/test_state.py`

- [ ] **Step 1: Write tests**

```python
# agent/tests/test_state.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from state import StateManager
from models import OrchestratorState, ComponentSpec, SearchResult


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.hset = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.delete = AsyncMock()
    r.lpush = AsyncMock()
    r.lrem = AsyncMock()
    r.lrange = AsyncMock(return_value=[])
    return r


@pytest.fixture
def state_mgr(mock_redis):
    return StateManager(mock_redis)


@pytest.mark.asyncio
async def test_save_and_load_roundtrip(state_mgr, mock_redis):
    state = OrchestratorState(
        task_id="t1", conversation_id="c1", user_id="u1",
        phase="awaiting_decision",
        components=[ComponentSpec(ref="R1", type="resistor", value="10k",
                                  package="0603", quantity_per_unit=4)],
    )
    await state_mgr.save(state)
    mock_redis.hset.assert_called_once()

    # Simulate load
    saved_data = mock_redis.hset.call_args[1] if mock_redis.hset.call_args[1] else {}
    # Extract the mapping from the call
    call_args = mock_redis.hset.call_args
    key = call_args[0][0]
    assert key == "agent:task_state:t1"


@pytest.mark.asyncio
async def test_pause_adds_to_paused_list(state_mgr, mock_redis):
    state = OrchestratorState(
        task_id="t1", conversation_id="c1", user_id="u1", phase="awaiting_decision",
    )
    await state_mgr.pause(state)
    mock_redis.lpush.assert_called_with("agent:paused", "t1")


@pytest.mark.asyncio
async def test_resume_cleans_up(state_mgr, mock_redis):
    await state_mgr.cleanup("t1")
    mock_redis.delete.assert_called_with("agent:task_state:t1")
    mock_redis.lrem.assert_called_with("agent:paused", 0, "t1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_state.py -v`
Expected: FAIL

- [ ] **Step 3: Create agent/state.py**

```python
"""Task state serialization for pause/resume."""

import json
import time

import structlog

from models import OrchestratorState

log = structlog.get_logger()

PAUSED_LIST_KEY = "agent:paused"
STATE_KEY_PREFIX = "agent:task_state:"
DECISIONS_KEY_PREFIX = "agent:decisions:"


class StateManager:
    """Manages orchestrator state in Redis for pause/resume."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def save(self, state: OrchestratorState) -> None:
        """Serialize orchestrator state to Redis hash."""
        key = f"{STATE_KEY_PREFIX}{state.task_id}"
        await self._redis.hset(key, mapping={"state": state.model_dump_json()})
        log.info("state.saved", task_id=state.task_id, phase=state.phase)

    async def load(self, task_id: str) -> OrchestratorState | None:
        """Load orchestrator state from Redis hash."""
        key = f"{STATE_KEY_PREFIX}{task_id}"
        data = await self._redis.hgetall(key)
        if not data:
            return None
        raw = data.get("state") or data.get(b"state")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return OrchestratorState.model_validate_json(raw)

    async def pause(self, state: OrchestratorState) -> None:
        """Save state and add task to paused list."""
        await self.save(state)
        # Store pause timestamp for auto-timeout
        await self._redis.hset(f"{STATE_KEY_PREFIX}{state.task_id}", "paused_at", str(time.time()))
        await self._redis.lpush(PAUSED_LIST_KEY, state.task_id)
        log.info("state.paused", task_id=state.task_id)

    async def cleanup(self, task_id: str) -> None:
        """Remove state and paused list entry after resume."""
        await self._redis.delete(f"{STATE_KEY_PREFIX}{task_id}")
        await self._redis.lrem(PAUSED_LIST_KEY, 0, task_id)
        await self._redis.delete(f"{DECISIONS_KEY_PREFIX}{task_id}")
        log.info("state.cleaned_up", task_id=task_id)

    async def get_paused_task_ids(self) -> list[str]:
        """Get all paused task IDs."""
        raw = await self._redis.lrange(PAUSED_LIST_KEY, 0, -1)
        return [x.decode() if isinstance(x, bytes) else x for x in raw]

    async def pop_decision(self, task_id: str, timeout: int = 5) -> dict | None:
        """Pop a user decision for a paused task (blocking with timeout)."""
        key = f"{DECISIONS_KEY_PREFIX}{task_id}"
        result = await self._redis.brpop(key, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
```

- [ ] **Step 4: Run tests**

Run: `cd agent && python -m pytest tests/test_state.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add agent/state.py agent/tests/test_state.py
git commit -m "feat(agent): add StateManager for pause/resume with Redis"
```

---

### Task 8: Create agent/search_agent.py

**Files:**
- Create: `agent/search_agent.py`
- Create: `agent/tests/test_search_agent.py`

- [ ] **Step 1: Write tests**

```python
# agent/tests/test_search_agent.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from search_agent import SearchAgent
from models import ComponentSpec


def _make_llm_response(content=None, tool_calls=None):
    """Helper to create a mock LLM response."""
    resp = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    resp.choices = [MagicMock()]
    resp.choices[0].message = msg
    resp.choices[0].finish_reason = "stop" if not tool_calls else "tool_calls"
    return resp


@pytest.mark.asyncio
async def test_search_agent_finds_component():
    """Sub-agent finds a component on first search."""
    spec = ComponentSpec(ref="R1", type="resistor", value="10k", package="0603",
                         tolerance="1%", quantity_per_unit=4)
    found_json = json.dumps({
        "status": "found", "ref": "R1", "mpn": "RC0603FR-0710KL",
        "manufacturer": "Yageo", "description": "10k 0603 1%",
        "unit_price": 0.004, "currency": "USD", "total_stock": 500000000,
        "distributor": "DigiKey", "distributor_stock": 100000,
        "distributor_url": "https://...", "octopart_url": "https://...",
    })

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=_make_llm_response(content=found_json))
    mock_router = AsyncMock()

    agent = SearchAgent(mock_llm, mock_router)
    result = await agent.search(spec, priority="price", production_volume=1000)

    assert result.status == "found"
    assert result.mpn == "RC0603FR-0710KL"


@pytest.mark.asyncio
async def test_search_agent_handles_tool_calls():
    """Sub-agent uses tools then returns result."""
    spec = ComponentSpec(ref="U1", type="op-amp", value="OPA1612", package="SOIC-8",
                         quantity_per_unit=2)

    # First response: tool call
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "search_parts"
    tool_call.function.arguments = '{"query": "OPA1612 op-amp SOIC-8"}'
    resp1 = _make_llm_response(tool_calls=[tool_call])

    # Second response: final answer
    found_json = json.dumps({
        "status": "found", "ref": "U1", "mpn": "OPA1612AIDR",
        "manufacturer": "TI", "description": "Op-amp",
        "unit_price": 4.23, "currency": "USD", "total_stock": 725546,
        "distributor": "DigiKey", "distributor_stock": 8674,
        "distributor_url": "https://...", "octopart_url": "https://...",
    })
    resp2 = _make_llm_response(content=found_json)

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=[resp1, resp2])
    mock_router = AsyncMock()
    mock_router.call_tool = AsyncMock(return_value='{"hits": 1, "results": [{"mpn": "OPA1612AIDR"}]}')

    agent = SearchAgent(mock_llm, mock_router)
    result = await agent.search(spec, priority="price", production_volume=1000)

    assert result.status == "found"
    assert result.mpn == "OPA1612AIDR"
    mock_router.call_tool.assert_called_once()


@pytest.mark.asyncio
async def test_search_agent_max_iterations():
    """Sub-agent stops after max iterations and returns error."""
    spec = ComponentSpec(ref="U1", type="ic", value="NONEXISTENT", package="QFP",
                         quantity_per_unit=1)

    # Always return tool calls (never a final answer)
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "search_parts"
    tool_call.function.arguments = '{"query": "NONEXISTENT"}'

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=_make_llm_response(tool_calls=[tool_call]))
    mock_router = AsyncMock()
    mock_router.call_tool = AsyncMock(return_value='{"hits": 0, "results": []}')

    agent = SearchAgent(mock_llm, mock_router, max_iterations=3)
    result = await agent.search(spec, priority="price", production_volume=1)

    assert result.status == "error"
    assert "max iterations" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_search_agent.py -v`
Expected: FAIL

- [ ] **Step 3: Create agent/search_agent.py**

```python
"""Focused search sub-agent with tool loop for finding one component."""

import json
from typing import Any

import structlog

from llm_client import LLMClient
from mcp_router import MCPRouter
from models import ComponentSpec, SearchResult
from prompts.search_agent import SEARCH_AGENT_SYSTEM_PROMPT

log = structlog.get_logger()

# Tools available to search sub-agents
SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": "Search for electronic components by description (e.g. '10k ohm resistor 0603 1%'). Returns top results with pricing and stock.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_mpn",
            "description": "Search for a specific component by Manufacturer Part Number. Returns detailed pricing and stock.",
            "parameters": {
                "type": "object",
                "properties": {"mpn": {"type": "string", "description": "Manufacturer Part Number"}},
                "required": ["mpn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_distributor",
            "description": "Search a distributor website for a component. Use only as fallback when Nexar returns nothing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "site": {"type": "string", "description": "Distributor site (mouser.com, digikey.com, etc.)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_product_page",
            "description": "Extract structured data from a distributor product page URL. Fallback only.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Product page URL"}},
                "required": ["url"],
            },
        },
    },
]


class SearchAgent:
    """Runs a focused tool loop to find one component."""

    def __init__(self, llm: LLMClient, router: MCPRouter, max_iterations: int = 10) -> None:
        self._llm = llm
        self._router = router
        self._max_iterations = max_iterations

    async def search(
        self,
        spec: ComponentSpec,
        priority: str = "price",
        production_volume: int = 1,
        context: str = "",
    ) -> SearchResult:
        """Search for a component matching the spec. Returns SearchResult."""
        spec_text = (
            f"Reference: {spec.ref}\n"
            f"Type: {spec.type}\n"
            f"Value: {spec.value}\n"
            f"Package: {spec.package}\n"
            f"Tolerance: {spec.tolerance}\n"
            f"Description: {spec.description}\n"
            f"Constraints: {json.dumps(spec.constraints)}\n"
            f"Quantity needed: {spec.quantity_per_unit * production_volume}\n"
            f"Priority: {priority}\n"
            f"Context: {context}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SEARCH_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": spec_text},
        ]

        for iteration in range(self._max_iterations):
            response = await self._llm.chat(messages=messages, tools=SEARCH_TOOLS)
            assistant_msg = response.choices[0].message

            # Append assistant message to history
            msg_dict: dict[str, Any] = {"role": "assistant", "content": assistant_msg.content or ""}
            if assistant_msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in assistant_msg.tool_calls
                ]
            messages.append(msg_dict)

            # No tool calls = final answer
            if not assistant_msg.tool_calls:
                return self._parse_result(assistant_msg.content or "", spec.ref)

            # Execute tool calls
            for tool_call in assistant_msg.tool_calls:
                name = tool_call.function.name
                try:
                    args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                try:
                    result = await self._router.call_tool(name, args)
                    result_str = result if isinstance(result, str) else json.dumps(result)
                except Exception as e:
                    result_str = json.dumps({"error": str(e)})
                    log.warning("search_agent.tool_error", tool=name, error=str(e)[:200])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str[:50000],  # Truncate huge results
                })

        # Max iterations reached
        log.warning("search_agent.max_iterations", ref=spec.ref)
        return SearchResult(
            status="error", ref=spec.ref,
            reason=f"Max iterations ({self._max_iterations}) reached without finding a component",
        )

    def _parse_result(self, content: str, ref: str) -> SearchResult:
        """Parse LLM final answer into SearchResult."""
        try:
            raw = content.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            data = json.loads(raw)
            data.setdefault("ref", ref)
            return SearchResult(**data)
        except (json.JSONDecodeError, Exception) as e:
            log.warning("search_agent.parse_error", ref=ref, error=str(e)[:200])
            return SearchResult(status="error", ref=ref, reason=f"Failed to parse LLM response: {str(e)[:200]}")
```

- [ ] **Step 4: Run tests**

Run: `cd agent && python -m pytest tests/test_search_agent.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add agent/search_agent.py agent/tests/test_search_agent.py
git commit -m "feat(agent): add SearchAgent sub-agent with focused tool loop"
```

---

### Task 9: Create agent/orchestrator.py

This is the core — the phase-based orchestrator that replaces `agent_runner.py`.

**Files:**
- Create: `agent/orchestrator.py`
- Create: `agent/tests/test_orchestrator.py`

- [ ] **Step 1: Write tests**

```python
# agent/tests/test_orchestrator.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orchestrator import Orchestrator
from models import ComponentSpec, SearchResult, OrchestratorState, AgentResult, CADStatus


@pytest.fixture
def mock_llm():
    return AsyncMock()


@pytest.fixture
def mock_router():
    router = AsyncMock()
    router.call_tool = AsyncMock()
    return router


@pytest.fixture
def mock_state_mgr():
    return AsyncMock()


@pytest.fixture
def mock_publish():
    return AsyncMock()


@pytest.mark.asyncio
async def test_phase1_parse_pdf_attachment(mock_llm, mock_router, mock_state_mgr, mock_publish):
    """Phase 1 should render PDF and extract text from all pages."""
    mock_router.call_tool = AsyncMock(side_effect=[
        # render_pdf_pages
        json.dumps({"pages": ["temp/page_1.png", "temp/page_2.png"]}),
        # extract_text page 1
        json.dumps({"text": "R1 10k, C1 100nF"}),
        # extract_text page 2
        json.dumps({"text": "U1 OPA1612"}),
        # get_image_base64 page 1
        json.dumps({"base64": "abc123"}),
        # get_image_base64 page 2
        json.dumps({"base64": "def456"}),
    ])

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    images, texts = await orch._phase1_parse_attachments(
        [{"path": "uploads/test.pdf", "type": "application/pdf"}]
    )
    assert len(images) == 2
    assert len(texts) == 2
    assert "R1 10k" in texts[0]


@pytest.mark.asyncio
async def test_phase2_analyze_schematic(mock_llm, mock_router, mock_state_mgr, mock_publish):
    """Phase 2 should call LLM and return component list."""
    mock_llm.analyze_schematic = AsyncMock(return_value={
        "components": [
            {"ref": "R1", "type": "resistor", "value": "10k", "package": "0603",
             "tolerance": "1%", "quantity_per_unit": 4},
        ],
        "production_volume": 1000,
        "priority": "price",
        "context": "audio mixer",
    })

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    result = await orch._phase2_analyze_schematic(["img_b64"], ["text"], "find components")
    assert len(result["components"]) == 1
    assert result["production_volume"] == 1000


@pytest.mark.asyncio
async def test_phase6_assemble_bom(mock_llm, mock_router, mock_state_mgr, mock_publish):
    """Phase 6 should merge all results into BOM."""
    components = [ComponentSpec(ref="R1", type="resistor", value="10k",
                                 package="0603", quantity_per_unit=4)]
    search_results = [SearchResult(status="found", ref="R1", mpn="RC0603FR-0710KL",
                                    manufacturer="Yageo", description="10k 0603",
                                    unit_price=0.004, currency="USD", total_stock=500000000,
                                    distributor="DigiKey", distributor_stock=100000,
                                    distributor_url="https://...", octopart_url="https://...")]
    cad_statuses = [CADStatus(mpn="RC0603FR-0710KL", available=True, url="https://snapeda.com/...")]

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    bom = orch._phase6_assemble_bom(components, search_results, cad_statuses, [], 1000)
    assert len(bom) == 1
    assert bom[0].quantity_total == 4000
    assert bom[0].search_result.mpn == "RC0603FR-0710KL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL

- [ ] **Step 3: Create agent/orchestrator.py**

```python
"""Phase-based orchestrator that replaces the monolithic agent loop."""

import asyncio
import json
import uuid
from typing import Any, Callable, Coroutine

import structlog

from llm_client import LLMClient
from mcp_router import MCPRouter
from models import (
    AgentResult, BOMEntry, CADStatus, ComponentSpec,
    Decision, DecisionOption, OrchestratorState, SearchResult,
)
from prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT
from search_agent import SearchAgent
from state import StateManager

log = structlog.get_logger()

MAX_SEARCH_CONCURRENCY = 5


class Orchestrator:
    """Runs the 7-phase pipeline for component sourcing."""

    def __init__(
        self,
        llm: LLMClient,
        router: MCPRouter,
        state_mgr: StateManager,
        publish: Callable[..., Coroutine],
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
        attachments: list[dict],
        conversation_history: list[dict] | None = None,
    ) -> AgentResult:
        """Run the full orchestration pipeline."""

        # Phase 1: Parse attachments
        await self._publish(conversation_id, task_id, "status", "Analyzing uploaded files...")
        images, texts = await self._phase1_parse_attachments(attachments)

        if not images and not texts and not message.strip():
            return AgentResult(
                status="needs_clarification", task_id=task_id,
                message="I need a schematic (PDF or image) or a description of the components you need.",
            )

        # Phase 2: Analyze schematic
        await self._publish(conversation_id, task_id, "status", "Analyzing schematic...")
        analysis = await self._phase2_analyze_schematic(images, texts, message)

        components = [ComponentSpec(**c) for c in analysis.get("components", [])]
        production_volume = analysis.get("production_volume", 1)
        priority = analysis.get("priority", "price")
        context = analysis.get("context", "")

        if not components:
            return AgentResult(
                status="needs_clarification", task_id=task_id,
                message="I couldn't identify any components in the schematic. Could you provide more detail?",
            )

        # Phase 3: Search components
        count = len(components)
        await self._publish(conversation_id, task_id, "status", f"Searching for {count} components...")
        search_results = await self._phase3_search_components(
            components, priority, production_volume, context,
        )

        # Phase 4: Check CAD
        found_mpns = [r.mpn for r in search_results if r.is_found and r.mpn]
        cad_statuses: list[CADStatus] = []
        if found_mpns:
            await self._publish(conversation_id, task_id, "status", "Checking CAD model availability...")
            cad_statuses = await self._phase4_check_cad(found_mpns)

        # Phase 5: User decisions (if needed)
        decisions = self._build_decisions(search_results, cad_statuses)
        if decisions:
            state = OrchestratorState(
                task_id=task_id, conversation_id=conversation_id, user_id=user_id,
                phase="awaiting_decision", message=message,
                production_volume=production_volume, priority=priority, context=context,
                components=components, search_results=search_results,
                cad_statuses=cad_statuses, decisions=decisions,
            )
            return AgentResult(
                status="decision_required", task_id=task_id,
                message="Some components need your input before I can finalize the BOM.",
                decisions=decisions,
                data={"state": state.model_dump()},
            )

        # Phase 6: Assemble BOM
        await self._publish(conversation_id, task_id, "status", "Assembling BOM...")
        bom = self._phase6_assemble_bom(components, search_results, cad_statuses, [], production_volume)

        # Phase 7: Generate exports
        await self._publish(conversation_id, task_id, "status", "Generating export files...")
        export_files = await self._phase7_generate_exports(bom, conversation_id)

        return self._build_recommendation(task_id, bom, export_files, production_volume, priority)

    async def resume(
        self,
        state: OrchestratorState,
        user_decisions: dict[str, str],
    ) -> AgentResult:
        """Resume from Phase 5 after user decisions."""
        # Apply decisions
        for decision in state.decisions:
            choice = user_decisions.get(decision.decision_id)
            if choice:
                decision.resolved = True
                decision.chosen = choice

        await self._publish(state.conversation_id, state.task_id, "status", "Applying your choices...")

        # Phase 6
        bom = self._phase6_assemble_bom(
            state.components, state.search_results, state.cad_statuses,
            state.decisions, state.production_volume,
        )

        # Phase 7
        await self._publish(state.conversation_id, state.task_id, "status", "Generating export files...")
        export_files = await self._phase7_generate_exports(bom, state.conversation_id)

        await self._state_mgr.cleanup(state.task_id)

        return self._build_recommendation(state.task_id, bom, export_files, state.production_volume, state.priority)

    # --- Phase implementations ---

    async def _phase1_parse_attachments(self, attachments: list[dict]) -> tuple[list[str], list[str]]:
        """Phase 1: Render PDFs, extract text, get image base64."""
        images: list[str] = []
        texts: list[str] = []

        for att in attachments:
            path = att.get("path", "")
            att_type = att.get("type", "")

            if "pdf" in att_type.lower():
                try:
                    render_result = await self._router.call_tool(
                        "render_pdf_pages", {"pdf_path": path},
                    )
                    render_data = json.loads(render_result) if isinstance(render_result, str) else render_result
                    pages = render_data.get("pages", [])

                    for i, page_path in enumerate(pages):
                        try:
                            text_result = await self._router.call_tool(
                                "extract_text", {"pdf_path": path, "page_number": i + 1},
                            )
                            text_data = json.loads(text_result) if isinstance(text_result, str) else text_result
                            if text_data.get("text"):
                                texts.append(text_data["text"])
                        except Exception as e:
                            log.warning("phase1.extract_text_error", page=i + 1, error=str(e)[:200])

                        try:
                            img_result = await self._router.call_tool(
                                "get_image_base64", {"image_path": page_path},
                            )
                            img_data = json.loads(img_result) if isinstance(img_result, str) else img_result
                            if img_data.get("base64"):
                                images.append(img_data["base64"])
                        except Exception as e:
                            log.warning("phase1.image_error", page=i + 1, error=str(e)[:200])

                except Exception as e:
                    log.error("phase1.pdf_error", path=path, error=str(e)[:200])

            elif "image" in att_type.lower():
                try:
                    img_result = await self._router.call_tool(
                        "get_image_base64", {"image_path": path},
                    )
                    img_data = json.loads(img_result) if isinstance(img_result, str) else img_result
                    if img_data.get("base64"):
                        images.append(img_data["base64"])
                except Exception as e:
                    log.warning("phase1.image_error", path=path, error=str(e)[:200])

        return images, texts

    async def _phase2_analyze_schematic(
        self, images: list[str], texts: list[str], user_message: str,
    ) -> dict[str, Any]:
        """Phase 2: Single LLM call to analyze schematic."""
        return await self._llm.analyze_schematic(
            images, texts, user_message, ORCHESTRATOR_SYSTEM_PROMPT,
        )

    async def _phase3_search_components(
        self,
        components: list[ComponentSpec],
        priority: str,
        production_volume: int,
        context: str,
    ) -> list[SearchResult]:
        """Phase 3: Parallel sub-agent search with batch pre-search."""
        # Batch pre-search for components with known MPNs
        known_mpns = {c.ref: c.value for c in components if c.value and not c.value.replace(".", "").replace("-", "").isdigit()}
        pre_searched: dict[str, SearchResult] = {}

        if known_mpns:
            try:
                batch_result = await self._router.call_tool(
                    "multi_match", {"mpns": list(known_mpns.values())},
                )
                batch_data = json.loads(batch_result) if isinstance(batch_result, str) else batch_result
                results_map = batch_data.get("results", {})
                for ref, mpn in known_mpns.items():
                    if mpn in results_map and results_map[mpn].get("results"):
                        part = results_map[mpn]["results"][0]
                        best_seller, best_offer = self._pick_best_offer(part.get("sellers", []))
                        pre_searched[ref] = SearchResult(
                            status="found", ref=ref,
                            mpn=part.get("mpn", mpn),
                            manufacturer=part.get("manufacturer"),
                            description=part.get("description"),
                            unit_price=best_offer.get("price") if best_offer else None,
                            currency=best_offer.get("currency") if best_offer else None,
                            total_stock=part.get("total_avail", 0),
                            distributor=best_seller,
                            distributor_stock=best_offer.get("stock") if best_offer else None,
                            distributor_url=best_offer.get("url") if best_offer else None,
                            octopart_url=part.get("octopart_url"),
                            median_price_1000=part.get("median_price_1000"),
                        )
            except Exception as e:
                log.warning("phase3.batch_presearch_error", error=str(e)[:200])

        # Sub-agent search for remaining components
        remaining = [c for c in components if c.ref not in pre_searched]
        semaphore = asyncio.Semaphore(MAX_SEARCH_CONCURRENCY)

        async def _search_one(spec: ComponentSpec) -> SearchResult:
            async with semaphore:
                agent = SearchAgent(self._llm, self._router)
                return await agent.search(spec, priority, production_volume, context)

        sub_results = await asyncio.gather(
            *[_search_one(c) for c in remaining],
            return_exceptions=True,
        )

        all_results: list[SearchResult] = list(pre_searched.values())
        for i, result in enumerate(sub_results):
            if isinstance(result, Exception):
                ref = remaining[i].ref if i < len(remaining) else f"unknown_{i}"
                log.error("phase3.sub_agent_error", ref=ref, error=str(result)[:200])
                all_results.append(SearchResult(status="error", ref=ref, reason=str(result)[:200]))
            else:
                all_results.append(result)

        return all_results

    async def _phase4_check_cad(self, mpns: list[str]) -> list[CADStatus]:
        """Phase 4: Batch CAD availability check."""
        try:
            result = await self._router.call_tool("check_cad_batch", {"mpns": mpns})
            data = json.loads(result) if isinstance(result, str) else result
            statuses = []
            for mpn, status in data.items():
                statuses.append(CADStatus(
                    mpn=mpn,
                    available=status.get("available", False),
                    url=status.get("url"),
                    formats=status.get("formats", []),
                ))
            return statuses
        except Exception as e:
            log.warning("phase4.cad_check_error", error=str(e)[:200])
            return [CADStatus(mpn=mpn, available=False) for mpn in mpns]

    def _build_decisions(
        self, search_results: list[SearchResult], cad_statuses: list[CADStatus],
    ) -> list[Decision]:
        """Build decision list from missing CAD models and unfound components."""
        decisions: list[Decision] = []
        cad_map = {s.mpn: s for s in cad_statuses}

        for sr in search_results:
            if sr.is_found and sr.mpn and sr.mpn in cad_map:
                cad = cad_map[sr.mpn]
                if not cad.available:
                    decisions.append(Decision(
                        decision_id=str(uuid.uuid4())[:8],
                        ref=sr.ref, mpn=sr.mpn,
                        issue="no_cad_model",
                        question=f"{sr.mpn} ({sr.manufacturer}) has no CAD model on SnapMagic",
                        options=[
                            DecisionOption(key="A", label="Add without CAD model"),
                            DecisionOption(key="B", label="I'll find an alternative myself"),
                        ],
                    ))
        return decisions

    def _phase6_assemble_bom(
        self,
        components: list[ComponentSpec],
        search_results: list[SearchResult],
        cad_statuses: list[CADStatus],
        decisions: list[Decision],
        production_volume: int,
    ) -> list[BOMEntry]:
        """Phase 6: Merge everything into BOM entries."""
        result_map = {r.ref: r for r in search_results}
        cad_map = {s.mpn: s for s in cad_statuses}

        bom: list[BOMEntry] = []
        for comp in components:
            sr = result_map.get(comp.ref, SearchResult(status="not_found", ref=comp.ref, reason="No search result"))
            cad = cad_map.get(sr.mpn) if sr.mpn else None
            bom.append(BOMEntry(
                ref=comp.ref,
                component=comp,
                search_result=sr,
                cad_status=cad,
                quantity_total=comp.quantity_per_unit * production_volume,
            ))
        return bom

    async def _phase7_generate_exports(
        self, bom: list[BOMEntry], conversation_id: str,
    ) -> list[str]:
        """Phase 7: Generate CSV/KiCad/Altium exports."""
        export_files: list[str] = []

        # Build export data
        export_components = []
        for entry in bom:
            if entry.search_result.is_found:
                export_components.append({
                    "ref": entry.ref,
                    "mpn": entry.search_result.mpn,
                    "manufacturer": entry.search_result.manufacturer,
                    "description": entry.search_result.description,
                    "quantity": entry.quantity_total,
                    "unit_price": entry.search_result.unit_price,
                    "currency": entry.search_result.currency,
                })

        bom_summary = {"conversation_id": conversation_id, "component_count": len(export_components)}

        for tool_name in ["generate_csv", "generate_kicad_library", "generate_altium_library"]:
            try:
                args = {"components": export_components}
                if tool_name == "generate_csv":
                    args["bom_summary"] = bom_summary
                result = await self._router.call_tool(tool_name, args)
                data = json.loads(result) if isinstance(result, str) else result
                if data.get("file_path"):
                    export_files.append(data["file_path"])
            except Exception as e:
                log.warning("phase7.export_error", tool=tool_name, error=str(e)[:200])

        return export_files

    def _build_recommendation(
        self, task_id: str, bom: list[BOMEntry], export_files: list[str],
        production_volume: int, priority: str,
    ) -> AgentResult:
        """Build the final recommendation AgentResult."""
        bom_data = []
        for entry in bom:
            bom_data.append({
                "ref": entry.ref,
                "type": entry.component.type,
                "value": entry.component.value,
                "package": entry.component.package,
                "mpn": entry.search_result.mpn,
                "manufacturer": entry.search_result.manufacturer,
                "description": entry.search_result.description,
                "unit_price": entry.search_result.unit_price,
                "currency": entry.search_result.currency,
                "total_stock": entry.search_result.total_stock,
                "distributor": entry.search_result.distributor,
                "distributor_url": entry.search_result.distributor_url,
                "octopart_url": entry.search_result.octopart_url,
                "quantity_per_unit": entry.component.quantity_per_unit,
                "quantity_total": entry.quantity_total,
                "cad_available": entry.cad_status.available if entry.cad_status else None,
                "cad_url": entry.cad_status.url if entry.cad_status else None,
                "status": entry.search_result.status,
                "reason": entry.search_result.reason,
            })

        found = sum(1 for b in bom_data if b["status"] == "found")
        total = len(bom_data)

        return AgentResult(
            status="recommendation",
            task_id=task_id,
            message=f"Found {found}/{total} components for your BOM.",
            data={
                "bom": bom_data,
                "production_volume": production_volume,
                "priority": priority,
                "export_files": export_files,
            },
        )

    @staticmethod
    def _pick_best_offer(sellers: list[dict]) -> tuple[str | None, dict | None]:
        """Pick the seller/offer with lowest price and stock > 0."""
        best_seller = None
        best_offer: dict | None = None
        best_price = float("inf")

        for seller in sellers:
            for offer in seller.get("offers", []):
                stock = offer.get("stock", 0) or 0
                if stock <= 0:
                    continue
                for price_break in offer.get("prices", []):
                    p = price_break.get("price", 0) or 0
                    if 0 < p < best_price:
                        best_price = p
                        best_seller = seller.get("name")
                        best_offer = {
                            "stock": stock,
                            "price": p,
                            "currency": price_break.get("currency", "USD"),
                            "url": offer.get("url"),
                        }
        return best_seller, best_offer
```

- [ ] **Step 4: Run tests**

Run: `cd agent && python -m pytest tests/test_orchestrator.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add agent/orchestrator.py agent/tests/test_orchestrator.py
git commit -m "feat(agent): add Orchestrator with 7-phase pipeline"
```

---

### Task 10: Rewrite agent/worker.py and agent/main.py

Replace the current worker (with its post-processing logic) with a simplified version that delegates to the orchestrator and handles decision resume.

**Files:**
- Modify: `agent/worker.py`
- Modify: `agent/main.py`
- Modify: `agent/mcp_router.py`
- Delete: `agent/agent_runner.py`
- Delete: `agent/prompts.py`
- Modify: `agent/requirements.txt`
- Modify: `agent/Dockerfile`
- Modify: `agent/tests/test_worker.py`

- [ ] **Step 1: Update requirements.txt**

Replace contents of `agent/requirements.txt`:

```
openai>=1.0.0
redis[hiredis]>=5.0.0
mcp>=1.0.0
httpx>=0.27.0
structlog>=24.0.0
python-dotenv>=1.0.0
Pillow>=10.0.0
pydantic>=2.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Note: `mcp` SDK kept (MCPRouter still uses it for SSE transport). `pydantic` added.

- [ ] **Step 2: Update mcp_router.py**

Clean up `_TOOL_SERVER_MAP` — remove tools that no longer exist, keep the mapping current:

```python
_TOOL_SERVER_MAP = {
    # mcp-documents
    "render_pdf_pages": "mcp-documents",
    "get_image_base64": "mcp-documents",
    "crop_zoom_image": "mcp-documents",
    "extract_text": "mcp-documents",
    # mcp-nexar
    "search_parts": "mcp-nexar",
    "search_mpn": "mcp-nexar",
    "multi_match": "mcp-nexar",
    # mcp-snapmagic
    "check_cad_availability": "mcp-snapmagic",
    "check_cad_batch": "mcp-snapmagic",
    # mcp-websearch
    "search_distributor": "mcp-websearch",
    "fetch_product_page": "mcp-websearch",
    # mcp-export
    "generate_csv": "mcp-export",
    "generate_kicad_library": "mcp-export",
    "generate_altium_library": "mcp-export",
}
```

Remove `check_lifecycle`, `get_quota_status`, `get_part_details`, `check_stock` from the map.

- [ ] **Step 3: Rewrite worker.py**

Replace the entire file with a simplified version:

```python
"""Redis queue consumer that delegates to the Orchestrator."""

import asyncio
import json
import time
from typing import Any

import redis.asyncio as aioredis
import structlog

from llm_client import LLMClient
from mcp_router import MCPRouter
from models import AgentResult, OrchestratorState
from orchestrator import Orchestrator
from state import StateManager

log = structlog.get_logger()


class AgentWorker:
    """Consumes tasks from Redis, delegates to Orchestrator."""

    def __init__(self, redis_url: str, max_concurrent: int = 50) -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._llm = LLMClient()
        self._router = MCPRouter()
        self._state_mgr: StateManager | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._redis_url, decode_responses=False)
        self._state_mgr = StateManager(self._redis)
        log.info("worker.connected", redis_url=self._redis_url)

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Main loop: consume tasks and listen for decisions."""
        requeued = await self._requeue_orphaned_tasks()
        if requeued:
            log.info("worker.requeued_orphaned", count=requeued)

        # Recover paused tasks on startup
        await self._recover_paused_tasks()

        # Start decision listener in background
        decision_task = asyncio.create_task(self._decision_listener(shutdown_event))

        try:
            while not shutdown_event.is_set():
                try:
                    result = await self._redis.blmove(
                        "agent:tasks", "agent:processing", 1, "LEFT", "RIGHT",
                    )
                except Exception:
                    if shutdown_event.is_set():
                        break
                    await asyncio.sleep(1)
                    continue

                if result is None:
                    continue

                raw = result.decode() if isinstance(result, bytes) else result
                task = json.loads(raw)
                async with self._semaphore:
                    asyncio.create_task(self._process_task(task, raw))
        finally:
            decision_task.cancel()
            try:
                await decision_task
            except asyncio.CancelledError:
                pass

    async def _process_task(self, task: dict, raw_task: str) -> None:
        """Process a single task via the Orchestrator."""
        task_id = task.get("task_id", "unknown")
        conversation_id = task.get("conversation_id", "unknown")

        try:
            orch = Orchestrator(self._llm, self._router, self._state_mgr, self._publish)
            result = await orch.run(
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=task.get("user_id", ""),
                message=task.get("message", ""),
                attachments=task.get("attachments", []),
                conversation_history=task.get("conversation_history"),
            )

            if result.status == "decision_required":
                # Pause: save state, move to paused list
                state = OrchestratorState(**result.data["state"])
                await self._state_mgr.pause(state)
                await self._publish(
                    conversation_id, task_id, "decision_required",
                    result.model_dump(),
                )
                # Move from processing to paused
                await self._redis.lrem("agent:processing", 1, raw_task)
                return

            await self._publish(conversation_id, task_id, "result", result.model_dump())

        except Exception as e:
            log.error("worker.task_error", task_id=task_id, error=str(e)[:500])
            await self._publish(
                conversation_id, task_id, "error", {"error": str(e)[:500]},
            )
        finally:
            await self._redis.lrem("agent:processing", 1, raw_task)

    async def _decision_listener(self, shutdown_event: asyncio.Event) -> None:
        """Background loop: poll paused tasks for user decisions + auto-timeout."""
        while not shutdown_event.is_set():
            try:
                paused_ids = await self._state_mgr.get_paused_task_ids()
                for task_id in paused_ids:
                    # Check for auto-timeout (30 minutes)
                    state = await self._state_mgr.load(task_id)
                    if state:
                        paused_at = await self._redis.hget(f"agent:task_state:{task_id}", "paused_at")
                        if paused_at:
                            elapsed = time.time() - float(paused_at)
                            if elapsed > 1800:  # 30 minutes
                                log.info("worker.auto_timeout", task_id=task_id, elapsed_s=int(elapsed))
                                # Auto-select first option for each decision
                                auto_decisions = {
                                    d.decision_id: d.options[0].key
                                    for d in state.decisions if d.options
                                }
                                asyncio.create_task(self._resume_task(task_id, auto_decisions))
                                continue

                    decision = await self._state_mgr.pop_decision(task_id, timeout=1)
                    if decision:
                        asyncio.create_task(self._resume_task(task_id, decision))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("worker.decision_listener_error", error=str(e)[:200])
            await asyncio.sleep(2)

    async def _resume_task(self, task_id: str, user_decisions: dict) -> None:
        """Resume a paused task with user decisions."""
        try:
            async with self._semaphore:
                state = await self._state_mgr.load(task_id)
                if not state:
                    log.warning("worker.resume_no_state", task_id=task_id)
                    return

                orch = Orchestrator(self._llm, self._router, self._state_mgr, self._publish)
                result = await orch.resume(state, user_decisions)
                await self._publish(state.conversation_id, task_id, "result", result.model_dump())
        except Exception as e:
            log.error("worker.resume_error", task_id=task_id, error=str(e)[:500])

    async def _publish(self, conversation_id: str, task_id: str, msg_type: str, data: Any = None) -> None:
        """Publish status/result/error to Redis pub/sub."""
        message = json.dumps({
            "task_id": task_id,
            "type": msg_type,
            **({"text": data} if isinstance(data, str) else {"data": data} if data else {}),
        })
        await self._redis.publish(f"agent:status:{conversation_id}", message)

    async def _requeue_orphaned_tasks(self) -> int:
        """Move any tasks stuck in processing back to the queue."""
        count = 0
        while True:
            task = await self._redis.rpoplpush("agent:processing", "agent:tasks")
            if task is None:
                break
            count += 1
        return count

    async def _recover_paused_tasks(self) -> None:
        """On startup, check paused tasks and re-publish decision requests."""
        paused_ids = await self._state_mgr.get_paused_task_ids()
        for task_id in paused_ids:
            decision = await self._state_mgr.pop_decision(task_id, timeout=0)
            if decision:
                asyncio.create_task(self._resume_task(task_id, decision))
            else:
                state = await self._state_mgr.load(task_id)
                if state and state.decisions:
                    await self._publish(
                        state.conversation_id, task_id, "decision_required",
                        AgentResult(
                            status="decision_required", task_id=task_id,
                            message="Waiting for your input on component decisions.",
                            decisions=state.decisions,
                        ).model_dump(),
                    )
```

Note: The `from typing import Any` import is needed at the top.

- [ ] **Step 4: Update main.py**

Replace `agent/main.py`:

```python
"""Agent service entrypoint."""

import asyncio
import os
import signal

import structlog
from dotenv import load_dotenv

load_dotenv()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


async def main() -> None:
    from worker import AgentWorker

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    max_concurrent = int(os.environ.get("AGENT_MAX_CONCURRENT_TASKS", "50"))

    worker = AgentWorker(redis_url, max_concurrent)
    await worker.connect()

    shutdown_event = asyncio.Event()

    def handle_signal(sig, _frame):
        log.info("worker.shutdown_signal", signal=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("worker.starting", redis_url=redis_url, max_concurrent=max_concurrent)
    try:
        await worker.run(shutdown_event)
    finally:
        await worker.close()
        log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Delete old files**

```bash
rm agent/agent_runner.py agent/prompts.py
```

- [ ] **Step 6: Run all agent tests**

Run: `cd agent && python -m pytest tests/ -v`
Expected: ALL PASS (some old tests may need updating — see next step)

- [ ] **Step 7: Fix any failing tests in test_worker.py**

Update `agent/tests/test_worker.py` to test the new simplified worker (task consumption, publish, decision listener). Remove tests for old post-processing methods.

- [ ] **Step 8: Commit**

```bash
git add agent/
git commit -m "feat(agent): rewrite worker with orchestrator, delete old agent_runner"
```

---

### Task 11: Backend Decision Routing

**Files:**
- Modify: `backend/services/task_manager.py`
- Modify: `backend/routers/messages.py`
- Modify: `backend/websocket/manager.py`
- Modify: `backend/services/redis_client.py`

- [ ] **Step 1: Add submit_decision to task_manager.py**

`task_manager.py` uses module-level functions (not a class). Add a standalone function:

```python
async def submit_decision(task_id: str, decision_data: dict) -> None:
    """Push a user decision to the agent's decision queue."""
    redis = await redis_client.get_client()
    key = f"agent:decisions:{task_id}"
    await redis.lpush(key, json.dumps(decision_data))
    log.info("task_manager.decision_submitted", task_id=task_id)
```

- [ ] **Step 2: Add decision fields to SendMessageRequest**

The existing `SendMessageRequest` Pydantic model only has `content`, `attachments`, `upload_ids`. Add optional decision fields:

```python
class SendMessageRequest(BaseModel):
    content: str = ""
    attachments: list[dict] = []
    upload_ids: list[str] = []
    # Decision response fields (optional)
    decision_id: str | None = None
    task_id: str | None = None
    choice: str | None = None
```

- [ ] **Step 3: Update messages.py to detect and route decisions**

In `routers/messages.py`, in the `send_message` endpoint, add decision detection before the normal task submission:

```python
# After parsing the request body into SendMessageRequest
if body.decision_id:
    if not body.task_id or not body.choice:
        raise HTTPException(400, "decision_id requires task_id and choice")
    await task_manager.submit_decision(body.task_id, {
        "decision_id": body.decision_id,
        "choice": body.choice,
    })
    # Save user decision as a message in Supabase
    user_msg = await supabase_client.create_message(
        conversation_id=conversation_id,
        role="user",
        content=json.dumps({"decision_id": body.decision_id, "choice": body.choice}),
    )
    return JSONResponse({"message": user_msg, "type": "decision_response"})
```

- [ ] **Step 3: Update _generate_title to use OpenAI directly**

Replace the LiteLLM proxy call in `_generate_title()` with a direct `httpx` call to OpenAI:

```python
async def _generate_title(message: str) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Generate a short title (max 6 words) for this conversation. Return only the title, no quotes."},
                        {"role": "user", "content": message[:500]},
                    ],
                    "max_tokens": 20,
                },
            )
            data = response.json()
            return data["choices"][0]["message"]["content"].strip().strip('"')
    except Exception:
        return None
```

- [ ] **Step 4: Update websocket/manager.py for decision_required**

In the WebSocket message handler, add handling for `decision_required` type:

```python
# In the pub/sub listener, where message types are handled:
if msg_type == "decision_required":
    # Forward the full decision data to connected clients
    await self._send_to_conversation(conversation_id, {
        "type": "decision_required",
        "data": msg_data.get("data", {}),
    })
```

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(backend): add decision routing, fix _generate_title for direct OpenAI"
```

---

### Task 12: Frontend Decision Cards

**Files:**
- Modify: `frontend/components/MessageBubble.tsx`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/hooks/useWebSocket.ts`
- Modify: `frontend/components/StatusIndicator.tsx`

- [ ] **Step 1: Add sendDecision to api.ts**

Add to `frontend/lib/api.ts`:

```typescript
export async function sendDecision(
  conversationId: string,
  taskId: string,
  decisionId: string,
  choice: string,
): Promise<void> {
  await fetch(`${API_BASE}/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: `Decision: ${choice}`,
      decision_id: decisionId,
      task_id: taskId,
      choice,
    }),
  });
}
```

- [ ] **Step 2: Add DecisionCard component to MessageBubble.tsx**

Add a new component inside MessageBubble.tsx (or as a separate small component) that renders decision cards:

```tsx
function DecisionCard({
  decision,
  conversationId,
  taskId,
  onDecisionMade,
}: {
  decision: {
    decision_id: string;
    ref: string;
    mpn: string;
    question: string;
    options: { key: string; label: string }[];
    resolved: boolean;
    chosen: string | null;
  };
  conversationId: string;
  taskId: string;
  onDecisionMade: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(decision.chosen);
  const [loading, setLoading] = useState(false);

  const handleClick = async (key: string) => {
    if (selected) return;
    setLoading(true);
    try {
      await sendDecision(conversationId, taskId, decision.decision_id, key);
      setSelected(key);
      onDecisionMade();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="border rounded-lg p-4 my-2 bg-amber-50 dark:bg-amber-950/20">
      <p className="font-medium text-sm mb-1">{decision.ref}: {decision.mpn}</p>
      <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">{decision.question}</p>
      <div className="flex gap-2 flex-wrap">
        {decision.options.map((opt) => (
          <button
            key={opt.key}
            onClick={() => handleClick(opt.key)}
            disabled={!!selected || loading}
            className={`px-3 py-1.5 rounded text-sm transition-colors ${
              selected === opt.key
                ? "bg-blue-600 text-white"
                : selected
                ? "bg-gray-100 text-gray-400 cursor-not-allowed"
                : "bg-white border border-gray-300 hover:bg-blue-50 hover:border-blue-300"
            }`}
          >
            {opt.key}: {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Render decision cards in MessageBubble**

In the message rendering logic, detect `decision_required` status and render `DecisionCard` for each decision:

```tsx
// Inside the assistant message rendering section:
if (parsedContent?.status === "decision_required") {
  return (
    <div>
      <p className="mb-2">{parsedContent.message}</p>
      {parsedContent.decisions?.map((d) => (
        <DecisionCard
          key={d.decision_id}
          decision={d}
          conversationId={conversationId}
          taskId={parsedContent.task_id}
          onDecisionMade={() => {/* refresh or update state */}}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Handle decision_required in useWebSocket.ts**

In the WebSocket message handler, add a case for `decision_required`:

```typescript
if (data.type === "decision_required") {
  // Treat as an assistant message with decision_required status
  onMessage?.({
    role: "assistant",
    content: JSON.stringify(data.data),
  });
}
```

- [ ] **Step 5: Update StatusIndicator for phase-aware messages**

Update `StatusIndicator.tsx` to show phase-specific messages (these come as `status` type messages from the agent). The current component likely already shows status text — just ensure it handles the new phase descriptions.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): add decision cards, sendDecision API, phase-aware status"
```

---

### Task 13: Docker & Infrastructure Cleanup

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove litellm-proxy from docker-compose.yml**

Delete the entire `litellm-proxy` service block. Then search for `litellm-proxy` in ALL other service blocks and remove it from every `depends_on` section. Affected services:
- `agent` — remove `litellm-proxy` from depends_on (keep redis, mcp-* dependencies)
- `backend` — remove `litellm-proxy` from depends_on (keep redis, minio, supabase dependencies)
- `mcp-websearch` — remove `litellm-proxy` from depends_on entirely (no other dependencies needed)

- [ ] **Step 2: Update agent service in docker-compose.yml**

Remove `LITELLM_BASE_URL` env var. Add new env vars. Keep all existing MCP server dependencies:

```yaml
agent:
  build: ./agent
  environment:
    - REDIS_URL=redis://redis:6379/0
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - OPENAI_MODEL=${OPENAI_MODEL:-gpt-5.4}
    - OPENAI_REASONING_EFFORT=${OPENAI_REASONING_EFFORT:-high}
    - AGENT_MAX_CONCURRENT_TASKS=${AGENT_MAX_CONCURRENT_TASKS:-50}
  depends_on:
    redis:
      condition: service_healthy
    mcp-nexar:
      condition: service_healthy
    mcp-snapmagic:
      condition: service_healthy
    mcp-documents:
      condition: service_healthy
    mcp-websearch:
      condition: service_healthy
    mcp-export:
      condition: service_healthy
```

- [ ] **Step 3: Update backend service**

Add `OPENAI_API_KEY` to backend environment. Remove `litellm-proxy` from depends_on:

```yaml
backend:
  environment:
    # ... existing vars ...
    - OPENAI_API_KEY=${OPENAI_API_KEY}
```

- [ ] **Step 4: Update mcp-websearch service**

Replace `LITELLM_BASE_URL` with `OPENAI_API_KEY`. Remove `depends_on: litellm-proxy`:

```yaml
mcp-websearch:
  environment:
    - OPENAI_API_KEY=${OPENAI_API_KEY}
  # No depends_on needed (litellm-proxy removed)
```

- [ ] **Step 5: Update mcp-snapmagic service**

Remove `LITELLM_BASE_URL` from environment.

- [ ] **Step 6: Remove litellm-proxy volume if any**

Check if there's a named volume for litellm. If yes, remove it.

- [ ] **Step 7: Update .env.example**

Remove `LITELLM_BASE_URL`. Add:
```
OPENAI_MODEL=gpt-5.4
OPENAI_REASONING_EFFORT=high
OPENAI_BASE_URL=https://api.openai.com/v1
NEXAR_COUNTRY=
NEXAR_CURRENCY=
```

Also add `NEXAR_COUNTRY` and `NEXAR_CURRENCY` to the mcp-nexar service environment in docker-compose.yml.

- [ ] **Step 8: Update CLAUDE.md**

Update the architecture section: 12 containers, remove litellm-proxy from the list, update the agent description, add note about interactive decisions.

- [ ] **Step 9: Commit**

```bash
git add docker-compose.yml .env.example CLAUDE.md
git commit -m "chore: remove litellm-proxy, update docker config for direct OpenAI"
```

---

### Task 14: Integration Test

**Files:**
- Create: `agent/tests/test_integration.py`

- [ ] **Step 1: Write integration test for the full pipeline**

```python
# agent/tests/test_integration.py
"""Integration test for the full orchestrator pipeline (mocked MCP + LLM)."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orchestrator import Orchestrator
from llm_client import LLMClient
from mcp_router import MCPRouter
from state import StateManager
from models import AgentResult


@pytest.mark.asyncio
async def test_full_pipeline_no_decisions():
    """Full pipeline: PDF → analyze → search → CAD → BOM → exports."""
    mock_llm = AsyncMock(spec=LLMClient)
    mock_router = AsyncMock(spec=MCPRouter)
    mock_state = AsyncMock(spec=StateManager)
    mock_publish = AsyncMock()

    # Phase 1: PDF rendering
    # Note: sub-agent LLM returns a direct answer (no tool_calls), so no
    # sub-agent call_tool calls happen. The sequence is:
    # Phase 1 (3 calls) → Phase 3 multi_match (1 call) → Phase 4 (1 call) → Phase 7 (3 calls)
    mock_router.call_tool = AsyncMock(side_effect=[
        # Phase 1: render_pdf_pages
        json.dumps({"pages": ["temp/p1.png"]}),
        # Phase 1: extract_text page 1
        json.dumps({"text": "R1=10k, C1=100nF"}),
        # Phase 1: get_image_base64 page 1
        json.dumps({"base64": "iVBOR..."}),
        # Phase 3: multi_match (batch pre-search — "10k" is a value not an MPN, so no match)
        json.dumps({"results": {}, "errors": {"10k": "generic"}}),
        # Phase 4: check_cad_batch
        json.dumps({"RC0603FR-0710KL": {"available": True, "url": "https://snapeda.com/...", "formats": ["kicad"]}}),
        # Phase 7: generate_csv
        json.dumps({"file_path": "exports/bom.csv"}),
        # Phase 7: generate_kicad_library
        json.dumps({"file_path": "exports/lib.kicad_sym"}),
        # Phase 7: generate_altium_library
        json.dumps({"file_path": "exports/lib.SchLib"}),
    ])

    # Phase 2: LLM analyze schematic
    mock_llm.analyze_schematic = AsyncMock(return_value={
        "components": [{"ref": "R1", "type": "resistor", "value": "10k", "package": "0603", "tolerance": "1%", "quantity_per_unit": 4}],
        "production_volume": 1000,
        "priority": "price",
        "context": "test circuit",
    })

    # Phase 3: sub-agent LLM call (final answer)
    sub_agent_response = MagicMock()
    sub_agent_response.choices = [MagicMock()]
    sub_agent_response.choices[0].message.content = json.dumps({
        "status": "found", "ref": "R1", "mpn": "RC0603FR-0710KL",
        "manufacturer": "Yageo", "description": "10k 0603",
        "unit_price": 0.01, "currency": "USD", "total_stock": 500000000,
        "distributor": "DigiKey", "distributor_stock": 100000,
        "distributor_url": "https://...", "octopart_url": "https://...",
    })
    sub_agent_response.choices[0].message.tool_calls = None
    mock_llm.chat = AsyncMock(return_value=sub_agent_response)

    orch = Orchestrator(mock_llm, mock_router, mock_state, mock_publish)
    result = await orch.run(
        task_id="t1", conversation_id="c1", user_id="u1",
        message="Find components for this circuit",
        attachments=[{"path": "uploads/test.pdf", "type": "application/pdf"}],
    )

    assert result.status == "recommendation"
    assert len(result.data["bom"]) == 1
    assert result.data["bom"][0]["mpn"] == "RC0603FR-0710KL"
    assert len(result.data["export_files"]) == 3
```

- [ ] **Step 2: Run integration test**

Run: `cd agent && python -m pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add agent/tests/test_integration.py
git commit -m "test(agent): add integration test for full orchestrator pipeline"
```

---

### Task 15: Build and Smoke Test

- [ ] **Step 1: Build all containers**

Run: `docker compose build`
Expected: All 12 containers build successfully (no litellm-proxy)

- [ ] **Step 2: Start services**

Run: `docker compose up -d`
Expected: All 12 services start and pass health checks

- [ ] **Step 3: Check agent logs**

Run: `docker compose logs -f agent`
Expected: Agent starts, connects to Redis, no errors

- [ ] **Step 4: Send a test message via API**

```bash
curl -X POST http://localhost:8000/api/conversations \
  -H "Content-Type: application/json" \
  -d '{}' | jq '.id'

# Use the returned conversation ID
curl -X POST http://localhost:8000/api/conversations/<id>/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "I need a 10k ohm resistor in 0603 package, 1% tolerance, quantity 100"}'
```

Expected: 202 Accepted, then result appears via WebSocket

- [ ] **Step 5: Commit final state**

```bash
git add -A
git commit -m "chore: final cleanup after agent rewrite verification"
```
