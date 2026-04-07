"""Phase-based orchestrator that replaces the monolithic agent loop."""

import asyncio
import json
from typing import Any, Callable, Coroutine

import structlog


def _safe_json(result: Any) -> Any:
    """Parse MCP tool result to dict/list, tolerating non-JSON responses."""
    if isinstance(result, (dict, list)):
        return result
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            log.warning("safe_json_parse_failed", preview=text[:200])
            return {"raw": text}
    return {"raw": str(result)}

from llm_client import LLMClient
from mcp_router import MCPRouter
from models import (
    AgentResult, BOMEntry, ComponentSpec,
    Decision, DecisionOption, OrchestratorState, SearchResult,
)
from prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT
from search_agent import SearchAgent
from state import StateManager

log = structlog.get_logger()

# User-facing messages in supported languages
_MESSAGES = {
    "pl": {
        "need_schematic": "Potrzebuję schematu (obraz) lub opisu potrzebnych komponentów.",
        "no_components": "Nie udało się zidentyfikować komponentów na schemacie. Czy możesz podać więcej szczegółów?",
        "decisions_needed": "Niektóre komponenty wymagają Twojej decyzji przed finalizacją BOM.",
        "found": "Znaleziono {found}/{total} komponentów dla Twojego BOM.",
    },
    "en": {
        "need_schematic": "I need a schematic (image) or a description of the components you need.",
        "no_components": "I couldn't identify any components in the schematic. Could you provide more detail?",
        "decisions_needed": "Some components need your input before I can finalize the BOM.",
        "found": "Found {found}/{total} components for your BOM.",
    },
}


def _detect_lang(text: str) -> str:
    """Simple language detection based on common characters/words."""
    polish_chars = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    if any(c in polish_chars for c in text):
        return "pl"
    polish_words = {"jest", "się", "nie", "dla", "lub", "jak", "przy", "wszystkie", "elementy", "schemacie"}
    words = set(text.lower().split())
    if len(words & polish_words) >= 2:
        return "pl"
    return "en"


def _msg(lang: str, key: str, **kwargs: Any) -> str:
    msgs = _MESSAGES.get(lang, _MESSAGES["en"])
    return msgs[key].format(**kwargs)

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

        lang = _detect_lang(message)

        # Phase 1: Parse attachments
        att_count = len(attachments)
        if att_count > 0:
            await self._publish(conversation_id, task_id, "status",
                                f"Processing {att_count} uploaded file{'s' if att_count > 1 else ''}...")
        images, texts = await self._phase1_parse_attachments(attachments)

        if not images and not texts and not message.strip():
            return AgentResult(
                status="needs_clarification", task_id=task_id,
                message=_msg(lang, "need_schematic"),
            )

        # Phase 2: Analyze schematic
        page_info = f" ({len(images)} page{'s' if len(images) > 1 else ''})" if images else ""
        await self._publish(conversation_id, task_id, "status",
                            f"Analyzing schematic{page_info} — identifying components...")
        analysis = await self._phase2_analyze_schematic(images, texts, message)

        raw_components = [ComponentSpec(**c) for c in analysis.get("components", [])]
        components = self._dedup_components(raw_components)
        production_volume = analysis.get("production_volume", 1)
        priority = analysis.get("priority", "price")
        context = analysis.get("context", "")

        if not components:
            return AgentResult(
                status="needs_clarification", task_id=task_id,
                message=_msg(lang, "no_components"),
            )

        # Phase 3: Search components
        count = len(components)
        await self._publish(conversation_id, task_id, "status",
                            f"Searching TME for {count} component{'s' if count > 1 else ''} "
                            f"(priority: {priority}, volume: {production_volume})...")
        search_results = await self._phase3_search_components(
            components, priority, production_volume, context,
        )

        found = sum(1 for r in search_results if r.is_found)
        await self._publish(conversation_id, task_id, "status",
                            f"Search complete — found {found}/{count} components. Assembling BOM...")

        # Phase 6: Assemble BOM
        bom = self._phase6_assemble_bom(components, search_results, [], production_volume)

        # Phase 7: Generate exports
        await self._publish(conversation_id, task_id, "status",
                            "Generating export files (CSV, KiCad, Altium)...")
        export_files = await self._phase7_generate_exports(bom, conversation_id)

        return self._build_recommendation(task_id, bom, export_files, production_volume, priority, lang)

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
            state.components, state.search_results,
            state.decisions, state.production_volume,
        )

        # Phase 7
        await self._publish(state.conversation_id, state.task_id, "status", "Generating export files...")
        export_files = await self._phase7_generate_exports(bom, state.conversation_id)

        await self._state_mgr.cleanup(state.task_id)

        return self._build_recommendation(state.task_id, bom, export_files, state.production_volume, state.priority)

    # --- Phase implementations ---

    async def _phase1_parse_attachments(self, attachments: list[dict]) -> tuple[list[str], list[str]]:
        """Phase 1: Get image base64 for each attachment (images only)."""
        images: list[str] = []
        texts: list[str] = []

        for att in attachments:
            path = att.get("path", "")
            # mcp-documents expects minio:// URIs — prefix if needed
            if path and not path.startswith("minio://"):
                path = f"minio://uploads/{path}" if not path.startswith("uploads/") else f"minio://{path}"
            att_type = att.get("type", "")
            # Infer type from file extension if not provided
            if not att_type:
                lower_path = path.lower()
                if any(lower_path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
                    att_type = "image"

            if "image" in att_type.lower():
                try:
                    img_result = await self._router.call_tool(
                        "get_image_base64", {"image_path": path},
                    )
                    img_data = _safe_json(img_result)
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
        image_urls = []
        for b64 in images:
            # Handle already-prefixed data URIs
            if b64.startswith("data:"):
                image_urls.append(b64)
            else:
                # Detect media type from base64 magic bytes
                media_type = "image/jpeg"
                if b64.startswith("iVBOR"):
                    media_type = "image/png"
                elif b64.startswith("R0lGOD"):
                    media_type = "image/gif"
                elif b64.startswith("UklGR"):
                    media_type = "image/webp"
                image_urls.append(f"data:{media_type};base64,{b64}")

        log.info("phase2.inputs", num_images=len(image_urls), num_texts=len(texts),
                 user_text_len=len(user_text), img_prefix=image_urls[0][:50] if image_urls else "none")

        result = await self._llm.analyze_schematic(
            ORCHESTRATOR_SYSTEM_PROMPT, user_text, image_urls,
        )
        log.info("phase2.result", num_components=len(result.get("components", [])),
                 keys=list(result.keys()), raw_preview=str(result)[:500])
        return result

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
                batch_data = _safe_json(batch_result)
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

    def _phase6_assemble_bom(
        self,
        components: list[ComponentSpec],
        search_results: list[SearchResult],
        decisions: list[Decision],
        production_volume: int,
    ) -> list[BOMEntry]:
        """Phase 6: Merge everything into BOM entries."""
        result_map = {r.ref: r for r in search_results}

        bom: list[BOMEntry] = []
        for comp in components:
            sr = result_map.get(comp.ref, SearchResult(status="not_found", ref=comp.ref, reason="No search result"))
            bom.append(BOMEntry(
                ref=comp.ref,
                component=comp,
                search_result=sr,
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
                data = _safe_json(result)
                if data.get("file_path"):
                    export_files.append(data["file_path"])
            except Exception as e:
                log.warning("phase7.export_error", tool=tool_name, error=str(e)[:200])

        return export_files

    def _build_recommendation(
        self, task_id: str, bom: list[BOMEntry], export_files: list[str],
        production_volume: int, priority: str, lang: str = "en",
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
                "quantity_per_unit": entry.component.quantity_per_unit,
                "quantity_total": entry.quantity_total,
                "status": entry.search_result.status,
                "reason": entry.search_result.reason,
            })

        found = sum(1 for b in bom_data if b["status"] == "found")
        total = len(bom_data)

        return AgentResult(
            status="recommendation",
            task_id=task_id,
            message=_msg(lang, "found", found=found, total=total),
            data={
                "bom": bom_data,
                "production_volume": production_volume,
                "priority": priority,
                "export_files": export_files,
            },
        )

    @staticmethod
    def _dedup_components(components: list[ComponentSpec]) -> list[ComponentSpec]:
        """Merge components with identical specs into single entries."""
        groups: dict[tuple, ComponentSpec] = {}
        for c in components:
            key = (c.type.lower(), c.value.lower(), c.package.lower(), c.tolerance.lower(),
                   json.dumps(c.constraints, sort_keys=True))
            if key in groups:
                existing = groups[key]
                existing.quantity_per_unit += c.quantity_per_unit
                existing.ref = f"{existing.ref}, {c.ref}"
                if c.description and not existing.description:
                    existing.description = c.description
            else:
                groups[key] = c.model_copy()
        deduped = list(groups.values())
        if len(deduped) < len(components):
            log.info("dedup_components", before=len(components), after=len(deduped))
        return deduped

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
