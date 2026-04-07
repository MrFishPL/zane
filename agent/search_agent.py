"""Focused search sub-agent with tool loop.

Dispatched by the orchestrator (one per component) to find a specific
electronic part.  Uses a subset of MCP tools (Nexar search + web
search fallback) and iterates until it finds a match or exhausts
the iteration budget.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from models import ComponentSpec, SearchResult

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Tool definitions available to the search sub-agent
# ---------------------------------------------------------------------------

SEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": "Search for electronic components on Nexar/Octopart by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. '100nF 0402 capacitor')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_mpn",
            "description": "Search for a specific manufacturer part number (MPN) on Nexar/Octopart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mpn": {
                        "type": "string",
                        "description": "Manufacturer part number to search",
                    },
                },
                "required": ["mpn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_distributor",
            "description": "Search for a component on distributor websites via web search. Fallback when Nexar returns no results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for the component",
                    },
                    "site": {
                        "type": "string",
                        "description": "Distributor site to search (e.g. 'digikey.com')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_product_page",
            "description": "Fetch and extract product information from a distributor product page URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the distributor product page",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

_SEARCH_SYSTEM_PROMPT = """\
You are a focused component search agent. Your ONLY job is to find a specific
electronic component and return its sourcing details.

You will receive a component specification. Use the available tools to search
for it on Nexar/Octopart and distributor websites.

## Search strategy
1. Start with the most specific query (exact value + package if known).
2. If no results, broaden progressively (drop package, use synonyms).
3. If Nexar fails after 3+ attempts, use search_distributor as fallback.
4. If a specific MPN is mentioned, try search_mpn first.

## CRITICAL rules
- Stock must be > 0.  Prefer parts with stock > required quantity.
- Price must be > 0.  Use actual prices from search results.
- MPN must come from search results — NEVER invent one.
- distributor_url must come from search results or be null.

## Response format
When you have found a suitable part (or exhausted your search budget),
respond with a JSON object (no markdown fences) with these fields:
{
  "status": "found" or "not_found" or "error",
  "ref": "<component reference>",
  "mpn": "<manufacturer part number or null>",
  "manufacturer": "<manufacturer or null>",
  "description": "<part description or null>",
  "unit_price": <float or null>,
  "currency": "<currency code or null>",
  "total_stock": <int or null>,
  "distributor": "<distributor name or null>",
  "distributor_stock": <int or null>,
  "distributor_url": "<url or null>",
  "octopart_url": "<url or null>",
  "median_price_1000": <object or null>,
  "constraints_reasoning": "<why this part matches or null>",
  "reason": "<reason if not_found/error or null>"
}
"""

# Maximum characters for a single tool result before truncation
_MAX_TOOL_RESULT_CHARS = 50_000


class SearchAgent:
    """Focused sub-agent that searches for a single component."""

    def __init__(
        self,
        llm_client,
        mcp_router,
        max_iterations: int = 10,
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

        for iteration in range(self._max_iterations):
            log.info("search_iteration", ref=spec.ref, iteration=iteration)

            response = await self._llm.chat(
                messages,
                tools=SEARCH_TOOLS,
            )

            # Append assistant message with raw content blocks
            messages.append({"role": "assistant", "content": response.content})

            # Check for tool use blocks
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                # No tool calls — extract text from content blocks
                text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text = block.text
                        break
                return self._parse_answer(text, spec.ref)

            # Execute each tool call and collect results
            tool_results = []
            for block in tool_use_blocks:
                tool_name = block.name
                arguments = block.input  # already a dict, not JSON string

                log.info(
                    "search_tool_call",
                    ref=spec.ref,
                    tool=tool_name,
                    arguments=arguments,
                    iteration=iteration,
                )

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

            # Send all tool results in a single user message
            messages.append({"role": "user", "content": tool_results})

        # Exhausted iteration budget
        log.warning("search_agent_max_iterations", ref=spec.ref, max=self._max_iterations)
        return SearchResult(
            status="error",
            ref=spec.ref,
            reason=f"Reached maximum iterations ({self._max_iterations}) without finding a part.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(
        spec: ComponentSpec,
        priority: str,
        production_volume: int,
        context: str,
    ) -> str:
        parts = [
            f"Find a sourcing match for this component:",
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

        parts.append(f"  Quantity per unit: {spec.quantity_per_unit}")
        parts.append(f"  Production volume: {production_volume}")
        parts.append(f"  Priority: {priority}")

        if context:
            parts.append(f"\nAdditional context: {context}")

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

        try:
            data = json.loads(content)
            if isinstance(data, dict):
                # Ensure ref is set
                data.setdefault("ref", ref)
                data.setdefault("status", "not_found")
                return SearchResult.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning(
                "search_answer_parse_error",
                ref=ref,
                error=str(exc),
                content_preview=content[:200],
            )

        return SearchResult(
            status="error",
            ref=ref,
            reason=f"Could not parse search agent response: {content[:200]}",
        )
