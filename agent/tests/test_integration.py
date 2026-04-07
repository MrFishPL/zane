"""Full pipeline integration test with mocked MCP + LLM.

Verifies the complete flow:
  PDF attachment
  -> Phase 1 (parse attachments)
  -> Phase 2 (analyze schematic)
  -> Phase 3 (search components)
  -> Phase 6 (assemble BOM)
  -> Phase 7 (generate exports)
  -> final recommendation result
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import AgentResult
from orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Shared constants for the test scenario
# ---------------------------------------------------------------------------

TASK_ID = "task-integration-001"
CONVERSATION_ID = "conv-integration-001"
USER_ID = "00000000-0000-0000-0000-000000000001"
USER_MESSAGE = "source the components from this schematic"

PDF_ATTACHMENT = {"path": "uploads/test-schematic.pdf", "type": "application/pdf"}

# Phase 1 MCP tool results
RENDER_PDF_RESULT = json.dumps({"pages": ["temp/page_1.png"]})
EXTRACT_TEXT_RESULT = json.dumps({"text": "R1 10k 0603"})
IMAGE_BASE64_RESULT = json.dumps({"base64": "iVBORw0KGgoAAAANS..."})

# Phase 2 LLM analysis result
ANALYSIS_RESULT = {
    "components": [
        {
            "ref": "R1",
            "type": "resistor",
            "description": "10k resistor 0603",
            "value": "10k",
            "package": "0603",
            "tolerance": "1%",
            "quantity_per_unit": 1,
        },
    ],
    "production_volume": 100,
    "priority": "price",
    "context": "test circuit",
}

# Phase 3 multi_match result -- empty because "10k" is not a valid MPN
MULTI_MATCH_RESULT = json.dumps({"results": {}})

# Phase 3 sub-agent LLM response -- direct final answer (no tool_calls)
SUB_AGENT_MPN = "RC0603FR-0710KL"
SUB_AGENT_ANSWER = json.dumps({
    "status": "found",
    "ref": "R1",
    "mpn": SUB_AGENT_MPN,
    "manufacturer": "Yageo",
    "description": "RES SMD 10K OHM 1% 1/10W 0603",
    "unit_price": 0.004,
    "currency": "USD",
    "total_stock": 500000000,
    "distributor": "DigiKey",
    "distributor_stock": 1200000,
    "distributor_url": "https://www.digikey.com/product-detail/RC0603FR-0710KL",
    "octopart_url": "https://octopart.com/rc0603fr-0710kl",
    "median_price_1000": None,
    "constraints_reasoning": "Matches 10k 0603 1% specification exactly.",
    "reason": None,
})

# Phase 7 export results
CSV_EXPORT_RESULT = json.dumps({"file_path": "exports/conv-integration-001/bom.csv"})
KICAD_EXPORT_RESULT = json.dumps({"file_path": "exports/conv-integration-001/library.kicad_sym"})
ALTIUM_EXPORT_RESULT = json.dumps({"file_path": "exports/conv-integration-001/library.IntLib"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_publish() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_state_mgr() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_router() -> AsyncMock:
    """MCPRouter mock with side_effect sequence for all phases.

    Call order:
      1. render_pdf_pages        (Phase 1)
      2. extract_text            (Phase 1)
      3. get_image_base64        (Phase 1)
      4. multi_match             (Phase 3 -- batch pre-search)
      5. generate_csv            (Phase 7)
      6. generate_kicad_library  (Phase 7)
      7. generate_altium_library (Phase 7)
    """
    router = AsyncMock()
    router.call_tool = AsyncMock(side_effect=[
        RENDER_PDF_RESULT,       # 1. Phase 1: render_pdf_pages
        EXTRACT_TEXT_RESULT,     # 2. Phase 1: extract_text
        IMAGE_BASE64_RESULT,     # 3. Phase 1: get_image_base64
        MULTI_MATCH_RESULT,      # 4. Phase 3: multi_match (empty -- "10k" is not an MPN)
        CSV_EXPORT_RESULT,       # 5. Phase 7: generate_csv
        KICAD_EXPORT_RESULT,     # 6. Phase 7: generate_kicad_library
        ALTIUM_EXPORT_RESULT,    # 7. Phase 7: generate_altium_library
    ])
    router.close = AsyncMock()
    return router


@pytest.fixture
def mock_llm() -> MagicMock:
    """LLMClient mock.

    - analyze_schematic (Phase 2): returns the component list JSON.
    - chat (Phase 3 sub-agent): returns an Anthropic-format response
      with text content and no tool_use blocks, so the sub-agent loop
      exits on the first iteration without making any MCPRouter calls.
    """
    llm = MagicMock()

    # Phase 2 -- Orchestrator calls llm.analyze_schematic(...)
    llm.analyze_schematic = AsyncMock(return_value=ANALYSIS_RESULT)

    # Phase 3 -- SearchAgent calls await llm.chat(messages, tools=...)
    # Build Anthropic-format response with text content, no tool_use blocks
    sub_agent_text = MagicMock()
    sub_agent_text.type = "text"
    sub_agent_text.text = SUB_AGENT_ANSWER

    sub_agent_response = MagicMock()
    sub_agent_response.content = [sub_agent_text]
    sub_agent_response.stop_reason = "end_turn"

    llm.chat = AsyncMock(return_value=sub_agent_response)

    return llm


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_pdf_to_recommendation(
    mock_llm, mock_router, mock_state_mgr, mock_publish,
):
    """End-to-end pipeline: PDF -> parse -> analyze -> search -> BOM -> exports."""

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)

    result: AgentResult = await orch.run(
        task_id=TASK_ID,
        conversation_id=CONVERSATION_ID,
        user_id=USER_ID,
        message=USER_MESSAGE,
        attachments=[PDF_ATTACHMENT],
    )

    # --- Core assertions ---
    assert result.status == "recommendation"
    assert result.task_id == TASK_ID

    bom = result.data["bom"]
    assert len(bom) == 1
    assert bom[0]["mpn"] == SUB_AGENT_MPN

    export_files = result.data["export_files"]
    assert len(export_files) == 3

    # --- Verify BOM entry details ---
    entry = bom[0]
    assert entry["ref"] == "R1"
    assert entry["type"] == "resistor"
    assert entry["value"] == "10k"
    assert entry["package"] == "0603"
    assert entry["manufacturer"] == "Yageo"
    assert entry["unit_price"] == 0.004
    assert entry["currency"] == "USD"
    assert entry["status"] == "found"

    # --- Verify export file paths ---
    assert "bom.csv" in export_files[0]
    assert "kicad_sym" in export_files[1]
    assert "IntLib" in export_files[2]

    # --- Verify production volume and priority from analysis ---
    assert result.data["production_volume"] == 100
    assert result.data["priority"] == "price"

    # --- Verify message mentions found count ---
    assert "1/1" in result.message

    # --- Verify publish was called for each phase status ---
    assert mock_publish.call_count >= 4  # at least: parse, analyze, search, BOM, exports

    # --- Verify MCP call_tool was called exactly 7 times ---
    assert mock_router.call_tool.call_count == 7

    # Verify the tool call sequence by tool name
    call_args_list = mock_router.call_tool.call_args_list
    tool_names = [call.args[0] for call in call_args_list]
    assert tool_names == [
        "render_pdf_pages",
        "extract_text",
        "get_image_base64",
        "multi_match",
        "generate_csv",
        "generate_kicad_library",
        "generate_altium_library",
    ]

    # --- Verify Phase 2: analyze_schematic was called once ---
    mock_llm.analyze_schematic.assert_called_once()

    # --- Verify Phase 3: sub-agent LLM was called once (direct answer, 1 iteration) ---
    mock_llm.chat.assert_called_once()
