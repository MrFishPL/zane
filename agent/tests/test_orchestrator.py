import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from orchestrator import Orchestrator
from models import BOMEntry, ComponentSpec, SearchResult, OrchestratorState, AgentResult


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
        # get_image_base64 page 1
        json.dumps({"base64": "abc123"}),
        # extract_text page 2
        json.dumps({"text": "U1 OPA1612"}),
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
async def test_phase1_parse_image_attachment(mock_llm, mock_router, mock_state_mgr, mock_publish):
    """Phase 1 should handle image attachments directly."""
    mock_router.call_tool = AsyncMock(return_value=json.dumps({"base64": "img_data_here"}))

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    images, texts = await orch._phase1_parse_attachments(
        [{"path": "uploads/schematic.png", "type": "image/png"}]
    )
    assert len(images) == 1
    assert images[0] == "img_data_here"
    assert len(texts) == 0


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

    # Verify analyze_schematic was called with correct args
    mock_llm.analyze_schematic.assert_called_once()
    call_args = mock_llm.analyze_schematic.call_args
    # First positional arg is system prompt, second is user text, third is image URLs
    assert "data:image/jpeg;base64,img_b64" in call_args[0][2]


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
    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    bom = orch._phase6_assemble_bom(components, search_results, [], 1000)
    assert len(bom) == 1
    assert bom[0].quantity_total == 4000
    assert bom[0].search_result.mpn == "RC0603FR-0710KL"


@pytest.mark.asyncio
async def test_phase6_missing_search_result(mock_llm, mock_router, mock_state_mgr, mock_publish):
    """Phase 6 should handle components with no search result."""
    components = [
        ComponentSpec(ref="R1", type="resistor", value="10k"),
        ComponentSpec(ref="C1", type="capacitor", value="100nF"),
    ]
    search_results = [SearchResult(status="found", ref="R1", mpn="RES-10K")]

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    bom = orch._phase6_assemble_bom(components, search_results, [], 1)
    assert len(bom) == 2
    assert bom[0].search_result.status == "found"
    assert bom[1].search_result.status == "not_found"


def test_pick_best_offer():
    """_pick_best_offer should return seller with lowest price and stock > 0."""
    sellers = [
        {
            "name": "DigiKey",
            "offers": [
                {
                    "stock": 10000,
                    "prices": [
                        {"price": 0.10, "currency": "USD"},
                        {"price": 0.05, "currency": "USD"},
                    ],
                    "url": "https://digikey.com/part1",
                },
            ],
        },
        {
            "name": "Mouser",
            "offers": [
                {
                    "stock": 5000,
                    "prices": [{"price": 0.08, "currency": "USD"}],
                    "url": "https://mouser.com/part1",
                },
            ],
        },
        {
            "name": "Broker",
            "offers": [
                {
                    "stock": 0,
                    "prices": [{"price": 0.01, "currency": "USD"}],
                    "url": "https://broker.com/part1",
                },
            ],
        },
    ]

    seller, offer = Orchestrator._pick_best_offer(sellers)
    assert seller == "DigiKey"
    assert offer["price"] == 0.05
    assert offer["stock"] == 10000


def test_pick_best_offer_no_stock():
    """_pick_best_offer should return None when no seller has stock."""
    sellers = [
        {
            "name": "DigiKey",
            "offers": [{"stock": 0, "prices": [{"price": 0.10, "currency": "USD"}]}],
        },
    ]
    seller, offer = Orchestrator._pick_best_offer(sellers)
    assert seller is None
    assert offer is None


def test_build_recommendation(mock_llm, mock_router, mock_state_mgr, mock_publish):
    """_build_recommendation should produce a valid AgentResult."""
    bom = [
        BOMEntry(
            ref="R1",
            component=ComponentSpec(ref="R1", type="resistor", value="10k", package="0603", quantity_per_unit=2),
            search_result=SearchResult(status="found", ref="R1", mpn="RC0603", manufacturer="Yageo",
                                       unit_price=0.01, currency="USD", total_stock=100000),
            quantity_total=2000,
        ),
    ]

    orch = Orchestrator(mock_llm, mock_router, mock_state_mgr, mock_publish)
    result = orch._build_recommendation("task-1", bom, ["/exports/bom.csv"], 1000, "price")

    assert result.status == "recommendation"
    assert result.task_id == "task-1"
    assert "1/1" in result.message
    assert result.data["production_volume"] == 1000
    assert len(result.data["bom"]) == 1
    assert result.data["bom"][0]["mpn"] == "RC0603"
    assert result.data["export_files"] == ["/exports/bom.csv"]
