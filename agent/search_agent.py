"""Focused search sub-agent with ReAct + Reflexion tool loop.

Dispatched by the orchestrator (one per component) to find a specific
electronic part on TME.  Uses structured reasoning (think tool),
TME search tools, and iterates with self-reflection until it finds
a match or exhausts the iteration budget.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from models import ComponentSpec, SearchResult

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tool definitions available to the search sub-agent (TME-only)
# ---------------------------------------------------------------------------

SEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Use this tool to plan your search strategy, reflect on failed attempts, "
                "or reason about which result best matches the spec. "
                "Call this BEFORE your first search and AFTER any failed search to adjust strategy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Your step-by-step reasoning about the search strategy or reflection on results",
                    },
                },
                "required": ["reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": (
                "Search for electronic components on TME by keyword. "
                "Returns top 5 results with pricing, stock, and TME product URLs. "
                "Tips: use simple terms like '100nF 0603 capacitor', '10uH inductor SMD', "
                "'SMA connector PCB'. TME search works best with value + type keywords."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. '100nF 0402 capacitor', '10k 0603 resistor')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_parts_in_category",
            "description": (
                "Search for components within a specific TME category. "
                "More precise than search_parts — use after finding the right category via get_categories. "
                "Especially useful for connectors, inductors, and other specialized components."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query within the category",
                    },
                    "category_id": {
                        "type": "string",
                        "description": "TME category ID (from get_categories)",
                    },
                },
                "required": ["query", "category_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_mpn",
            "description": "Search for a specific manufacturer part number (MPN) or TME symbol. Use when you know the exact part number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mpn": {
                        "type": "string",
                        "description": "Manufacturer part number or TME symbol to search",
                    },
                },
                "required": ["mpn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_categories",
            "description": (
                "Get TME category tree. Call with no arguments to get top-level categories, "
                "or pass parent_id to drill into subcategories. "
                "Returns category IDs, names, and product counts. "
                "Use this to find the right category for search_parts_in_category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_id": {
                        "type": "integer",
                        "description": "Parent category ID to get subcategories (omit for top-level)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": (
                "Get detailed technical parameters for specific TME product symbols. "
                "Returns resistance, capacitance, package, voltage rating, etc. "
                "Use this to verify that a found product actually matches the spec constraints."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of TME product symbols to get details for",
                    },
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_similar_products",
            "description": (
                "Find alternative/similar products for given TME symbols. "
                "Use when the best match is out of stock or too expensive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of TME product symbols to find alternatives for",
                    },
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_result",
            "description": (
                "Submit the final sourcing result. You MUST call this tool when done searching. "
                "Do NOT write a text response — ALWAYS use this tool to return your result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["found", "not_found"],
                        "description": "Whether a matching component was found on TME",
                    },
                    "ref": {"type": "string", "description": "Component reference designator"},
                    "mpn": {"type": "string", "description": "Manufacturer part number (from TME search results, never invented)"},
                    "manufacturer": {"type": "string", "description": "Manufacturer name"},
                    "description": {"type": "string", "description": "Short component description"},
                    "unit_price": {"type": "number", "description": "Unit price from TME"},
                    "currency": {"type": "string", "description": "Price currency (usually PLN)"},
                    "total_stock": {"type": "integer", "description": "Total stock on TME"},
                    "distributor": {"type": "string", "description": "Always 'TME'"},
                    "distributor_stock": {"type": "integer", "description": "Stock on TME"},
                    "distributor_url": {"type": "string", "description": "TME product URL from search results (tme_url field)"},
                    "constraints_reasoning": {"type": "string", "description": "Why this part matches (or doesn't match) the spec constraints"},
                    "reason": {"type": "string", "description": "Reason for not_found status"},
                },
                "required": ["status", "ref"],
            },
        },
    },
]

_SEARCH_SYSTEM_PROMPT = """\
You are an expert electronic component sourcing agent. Your job is to find \
a specific component on TME (Transfer Multisort Elektronik) that matches the \
given specification.

## METHOD: ReAct (Reason → Act → Observe → Reflect)

For EVERY search task, follow this loop:

1. **THINK FIRST** — Always call the `think` tool before your first search to plan your strategy. \
Analyze the component spec and decide which search approach to use.

2. **ACT** — Execute a search using one of the TME tools.

3. **OBSERVE** — Examine the results. Do any match the spec?

4. **REFLECT** — If results don't match, call `think` again to analyze WHY and adjust. \
Don't repeat the same query — change your approach based on what you learned.

5. **SUBMIT** — When you find a match (or exhaust all strategies), call `submit_result`.

## SEARCH STRATEGIES BY COMPONENT TYPE

### Resistors / Capacitors / Simple Passives
1. search_parts with value + package (e.g. "10k 0603 resistor")
2. If too many results, add tolerance or other constraints
3. If no results, try without package, then without tolerance

### Inductors
1. search_parts with value + package (e.g. "10uH 0805 inductor")
2. If no results, try broader: "10uH inductor SMD"
3. Browse categories: get_categories → find "Inductors" → search_parts_in_category
4. Verify with get_product_details to check current rating, DCR, etc.

### Connectors (SMA, USB, headers, terminal blocks, etc.)
Connectors are HARD to find by keyword. Use this strategy:
1. If MPN is known, search_mpn first
2. search_parts with connector type + key specs (e.g. "SMA female PCB edge mount")
3. If no results, get_categories → navigate to the connector subcategory → search_parts_in_category
4. Try different naming: "SMA jack" vs "SMA socket" vs "SMA connector", \
"pin header" vs "goldpin", "2-pin" vs "2 pin" vs "2P"

### ICs / Transistors / Specific Part Numbers
1. search_mpn with the exact part number
2. If not found, search_parts with the part number as query
3. Try common variations: with/without suffix (e.g. "LM317T" vs "LM317")

### LEDs / Diodes / Crystals / Switches
1. search_parts with type + key specs (e.g. "LED red 0603", "crystal 8MHz SMD")
2. If no results, try category browsing

## TME SEARCH TIPS
- TME keyword search works best with SHORT, SPECIFIC queries
- Include component TYPE in the query (resistor, capacitor, inductor, connector)
- Use standard value notation: 10k, 100nF, 4.7uH
- For packages: use common names (0603, 0805, SOT-23, DIP-8)
- If a query returns 0 results, try SHORTER and SIMPLER queries
- If a query returns too many irrelevant results, try CATEGORY search

## RULES
- ALL data must come from TME search results — NEVER invent MPNs, prices, or URLs
- Stock must be > 0
- Use the `tme_url` field from results for distributor_url
- distributor is always "TME"
- currency is usually "PLN" (TME default)
- When multiple results match, prefer: lowest price (if priority=price), \
highest stock (if priority=availability), best brand (if priority=quality)

## CRITICAL
- Call `think` before your first search to plan, and when you need to change strategy
- Do NOT call `think` on every iteration — only when you need to reason through a problem
- You can combine `think` with a search tool in the SAME turn to save iterations
- ALWAYS call `submit_result` to return your answer — never write plain text
- You have a limited budget — don't repeat failed queries, change approach instead
"""

# Maximum characters for a single tool result before truncation
_MAX_TOOL_RESULT_CHARS = 50_000


class SearchAgent:
    """Focused sub-agent that searches for a single component on TME."""

    def __init__(
        self,
        llm_client,
        mcp_router,
        max_iterations: int = 15,
    ) -> None:
        self._llm = llm_client
        self._router = mcp_router
        self._max_iterations = max_iterations

    async def search(
        self,
        spec: ComponentSpec,
        priority: str = "price",
        production_volume: int = 1,
        context: str = "",
    ) -> SearchResult:
        """Search for a component matching *spec*.

        Returns a :class:`SearchResult` with status ``found``,
        ``not_found``, or ``error``.
        """
        user_content = self._build_user_message(spec, priority, production_volume, context)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        log.info(
            "search_agent_start",
            ref=spec.ref,
            type=spec.type,
            value=spec.value,
        )

        search_iterations = 0
        total_turns = 0
        max_total_turns = self._max_iterations * 2  # hard cap to prevent infinite loops

        while search_iterations < self._max_iterations and total_turns < max_total_turns:
            total_turns += 1
            log.info("search_iteration", ref=spec.ref, iteration=search_iterations, turn=total_turns)

            response = await self._llm.chat(
                messages,
                tools=SEARCH_TOOLS,
            )

            # Append assistant message with raw content blocks
            messages.append({"role": "assistant", "content": response.content})

            # Check for tool use blocks
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                # No tool calls — extract text and try to parse as result
                text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text = block.text
                        break
                result = self._parse_answer(text, spec.ref)
                if result.status == "error" and "parse" in (result.reason or "").lower():
                    log.info("search_reformat", ref=spec.ref)
                    result = await self._reformat_answer(text, spec.ref)
                return result

            # Check if the model called submit_result (final answer tool)
            submit_blocks = [b for b in tool_use_blocks if b.name == "submit_result"]
            if submit_blocks:
                data = submit_blocks[0].input
                data.setdefault("ref", spec.ref)
                data.setdefault("status", "not_found")
                log.info("search_agent_submit", ref=spec.ref, status=data["status"])
                return SearchResult.model_validate(data)

            # Execute each tool call and collect results
            tool_results = []
            has_real_tool = False
            for block in tool_use_blocks:
                tool_name = block.name
                arguments = block.input

                log.info(
                    "search_tool_call",
                    ref=spec.ref,
                    tool=tool_name,
                    arguments=arguments,
                    iteration=search_iterations,
                )

                # Handle the think tool locally (no MCP call needed)
                if tool_name == "think":
                    log.info(
                        "search_agent_think",
                        ref=spec.ref,
                        reasoning=arguments.get("reasoning", "")[:300],
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Reasoning recorded. Proceed with your next action.",
                    })
                    continue

                has_real_tool = True
                try:
                    result = await self._router.call_tool(tool_name, arguments)
                    result_str = json.dumps(result) if not isinstance(result, str) else result
                except Exception as exc:
                    log.error(
                        "search_tool_error",
                        ref=spec.ref,
                        tool=tool_name,
                        error=str(exc),
                    )
                    result_str = json.dumps({"error": str(exc), "tool": tool_name})

                # Truncate oversized results
                if len(result_str) > _MAX_TOOL_RESULT_CHARS:
                    result_str = result_str[:_MAX_TOOL_RESULT_CHARS] + "...[truncated]"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            # Only count iterations that made real tool calls (not think-only)
            if has_real_tool:
                search_iterations += 1

            # Send all tool results in a single user message
            messages.append({"role": "user", "content": tool_results})

        # Exhausted iteration budget
        log.warning("search_agent_max_iterations", ref=spec.ref, search_iters=search_iterations, total_turns=total_turns)
        return SearchResult(
            status="error",
            ref=spec.ref,
            reason=f"Reached maximum iterations ({self._max_iterations}) without finding a part.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _reformat_answer(self, raw_text: str, ref: str) -> SearchResult:
        """Reformat a verbose LLM answer into structured JSON via a second call."""
        try:
            reformat_prompt = (
                "Extract the component sourcing data from the text below and return ONLY a raw JSON object.\n"
                "No explanations, no markdown, no commentary — just the JSON.\n\n"
                "Required fields: status (found/not_found/error), ref, mpn, manufacturer, "
                "description, unit_price, currency, total_stock, distributor, distributor_stock, "
                "distributor_url, constraints_reasoning, reason.\n"
                "Use null for unknown fields.\n\n"
                f"Text:\n{raw_text[:5000]}"
            )
            response = await self._llm.chat(
                [
                    {"role": "user", "content": reformat_prompt},
                ],
                max_tokens=1024,
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text = block.text
                    break
            data = json.loads(text.strip())
            data.setdefault("ref", ref)
            data.setdefault("status", "not_found")
            return SearchResult.model_validate(data)
        except Exception as exc:
            log.warning("search_reformat_error", ref=ref, error=str(exc)[:200])
            return SearchResult(
                status="error", ref=ref,
                reason=f"Could not parse search agent response: {raw_text[:200]}",
            )

    @staticmethod
    def _build_user_message(
        spec: ComponentSpec,
        priority: str,
        production_volume: int,
        context: str,
    ) -> str:
        parts = [
            f"Find this component on TME:",
            f"  Reference: {spec.ref}",
            f"  Type: {spec.type}",
        ]
        if spec.description:
            parts.append(f"  Description: {spec.description}")
        if spec.value:
            parts.append(f"  Value: {spec.value}")
        if spec.package:
            parts.append(f"  Package: {spec.package}")
        if spec.tolerance:
            parts.append(f"  Tolerance: {spec.tolerance}")
        if spec.constraints:
            constraints_str = ", ".join(f"{k}={v}" for k, v in spec.constraints.items())
            parts.append(f"  Constraints: {constraints_str}")

        parts.append(f"  Quantity needed: {spec.quantity_per_unit * production_volume}")
        parts.append(f"  Priority: {priority}")

        if context:
            parts.append(f"\nCircuit context: {context}")

        return "\n".join(parts)

    @staticmethod
    def _parse_answer(content: str, ref: str) -> SearchResult:
        """Parse the LLM's final answer into a SearchResult."""
        content = content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        # Try direct parse
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data.setdefault("ref", ref)
                data.setdefault("status", "not_found")
                return SearchResult.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to extract a JSON object embedded in verbose text
        first_brace = content.find("{")
        last_brace = content.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidate = content[first_brace : last_brace + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict) and "status" in data:
                    data.setdefault("ref", ref)
                    return SearchResult.model_validate(data)
            except (json.JSONDecodeError, ValueError):
                pass

        log.warning(
            "search_answer_parse_error",
            ref=ref,
            content_preview=content[:200],
        )

        return SearchResult(
            status="error",
            ref=ref,
            reason=f"Could not parse search agent response: {content[:200]}",
        )
