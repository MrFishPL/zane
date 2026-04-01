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
        """Phase 2: Single LLM call to analyze schematic.

        Assembles user text and image data URIs, then delegates to
        ``LLMClient.analyze_schematic``.
        """
        # Build combined user text from extracted texts and user message
        parts: list[str] = []
        if user_message.strip():
            parts.append(f"User request: {user_message.strip()}")
        if texts:
            parts.append("Extracted text from schematic pages:")
            for i, t in enumerate(texts, 1):
                parts.append(f"--- Page {i} ---\n{t}")
        user_text = "\n\n".join(parts) if parts else "Analyze the attached schematic."

        # Convert raw base64 strings to data URIs for the vision API
        image_urls = [
            f"data:image/png;base64,{b64}" for b64 in images
        ]

        return await self._llm.analyze_schematic(
            ORCHESTRATOR_SYSTEM_PROMPT, user_text, image_urls,
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
        # A value that is NOT purely numeric (e.g. "LM317T") is likely an MPN
        known_mpns = {
            c.ref: c.value
            for c in components
            if c.value and not c.value.replace(".", "").replace("-", "").isdigit()
        }
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
                args: dict[str, Any] = {"components": export_components}
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
