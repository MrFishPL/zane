"""Agent logic: LLM loop with MCP tool orchestration.

Uses the OpenAI SDK pointed at LiteLLM to drive GPT-5.4 with function
calling.  When the model emits tool calls, they are dispatched to the
appropriate MCP server via :mod:`mcp_router`.
"""

from __future__ import annotations

import json
import os
import asyncio
from typing import Any, Callable, Awaitable

import structlog
from openai import AsyncOpenAI

from mcp_router import MCPRouter
from prompts import SYSTEM_PROMPT

log = structlog.get_logger()

MODEL = "gpt-5.4"
MAX_LLM_RETRIES = 3
LLM_TIMEOUT_BASE = 30  # seconds; doubles on each retry

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    # ---- mcp-documents ----
    {
        "type": "function",
        "function": {
            "name": "render_pdf_pages",
            "description": "Render all pages of a PDF stored in MinIO to PNG images. Returns a list of image paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "MinIO object path to the PDF file",
                    }
                },
                "required": ["pdf_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_image_base64",
            "description": "Get the base64-encoded contents of an image stored in MinIO.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "MinIO object path to the image file",
                    }
                },
                "required": ["image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crop_zoom_image",
            "description": "Crop and zoom into a region of an image for closer inspection. Coordinates are percentages (0-100) of image dimensions. Returns base64 of the cropped region.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "MinIO URI of the source image (e.g. minio://temp/page_3.png)",
                    },
                    "x1_pct": {"type": "number", "description": "Left edge percentage (0-100)"},
                    "y1_pct": {"type": "number", "description": "Top edge percentage (0-100)"},
                    "x2_pct": {"type": "number", "description": "Right edge percentage (0-100)"},
                    "y2_pct": {"type": "number", "description": "Bottom edge percentage (0-100)"},
                },
                "required": ["image_path", "x1_pct", "y1_pct", "x2_pct", "y2_pct"],
            },
        },
    },
    # ---- mcp-nexar ----
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": "Search for electronic components on Nexar/Octopart by keyword, category, or specs. Returns pricing, stock, and distributor info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. '100nF 0402 capacitor')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10,
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
                    }
                },
                "required": ["mpn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_part_details",
            "description": "Get detailed information about a specific part including all specs, pricing tiers, and stock levels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "part_id": {
                        "type": "string",
                        "description": "Nexar/Octopart part ID",
                    }
                },
                "required": ["part_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check current stock levels for a part across all distributors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mpn": {
                        "type": "string",
                        "description": "Manufacturer part number",
                    }
                },
                "required": ["mpn"],
            },
        },
    },
    # ---- mcp-snapmagic ----
    {
        "type": "function",
        "function": {
            "name": "check_cad_availability",
            "description": "Check if CAD models (symbols, footprints) are available on SnapMagic/SnapEDA for a given MPN.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mpn": {
                        "type": "string",
                        "description": "Manufacturer part number to check",
                    }
                },
                "required": ["mpn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_cad_batch",
            "description": "Check CAD model availability on SnapMagic for multiple MPNs at once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mpns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of manufacturer part numbers to check",
                    }
                },
                "required": ["mpns"],
            },
        },
    },
    # ---- mcp-websearch ----
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
                    }
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
                    }
                },
                "required": ["url"],
            },
        },
    },
    # ---- mcp-export ----
    {
        "type": "function",
        "function": {
            "name": "generate_csv",
            "description": "Generate a CSV BOM file and upload to MinIO. Returns a dict with 'path' (MinIO URI) on success.",
            "parameters": {
                "type": "object",
                "properties": {
                    "components": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "mpn": {"type": "string"},
                                "qty_per_unit": {"type": "integer"},
                            },
                            "required": ["mpn", "qty_per_unit"],
                        },
                        "description": "List of component dicts with mpn, qty_per_unit, and any other BOM fields",
                    },
                    "volume": {
                        "type": "integer",
                        "description": "Production volume multiplier for quantities",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User ID for storage path",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Conversation ID for storage path",
                    },
                },
                "required": ["components", "volume", "user_id", "conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_kicad_library",
            "description": "Generate KiCad library ZIP (.kicad_sym + .kicad_mod) and upload to MinIO. Returns a dict with 'path' (MinIO URI) on success.",
            "parameters": {
                "type": "object",
                "properties": {
                    "components": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "mpn": {"type": "string"},
                                "description": {"type": "string"},
                                "datasheet_url": {"type": "string"},
                            },
                            "required": ["mpn"],
                        },
                        "description": "List of component dicts with mpn, description, datasheet_url, etc.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User ID for storage path",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Conversation ID for storage path",
                    },
                },
                "required": ["components", "user_id", "conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_altium_library",
            "description": "Generate Altium library ZIP (.SchLib + .PcbLib) and upload to MinIO. Returns a dict with 'path' (MinIO URI) on success.",
            "parameters": {
                "type": "object",
                "properties": {
                    "components": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "mpn": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["mpn"],
                        },
                        "description": "List of component dicts with mpn, description, etc.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User ID for storage path",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Conversation ID for storage path",
                    },
                },
                "required": ["components", "user_id", "conversation_id"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


class AgentRunner:
    """Runs a single agent task: LLM reasoning loop with MCP tool calls."""

    def __init__(
        self,
        litellm_base_url: str | None = None,
        mcp_router: MCPRouter | None = None,
        openai_client: AsyncOpenAI | None = None,
    ) -> None:
        base_url = litellm_base_url or os.environ.get(
            "LITELLM_BASE_URL", "http://litellm-proxy:4000"
        )
        self._client = openai_client or AsyncOpenAI(
            base_url=f"{base_url}/v1",
            api_key="not-needed",
        )
        self._router = mcp_router or MCPRouter()

    async def close(self) -> None:
        await self._router.close()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        conversation_history: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        conversation_id: str = "",
        on_status: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Execute the full agent flow and return the JSON response.

        Parameters
        ----------
        user_message:
            Latest user message text.
        conversation_history:
            Prior messages in OpenAI chat format.
        attachments:
            List of ``{"type": "pdf"|"image", "path": "<minio path>"}``.
        conversation_id:
            Used for export file naming.
        on_status:
            Async callback to publish progress updates.
        """
        messages = self._build_messages(
            user_message, conversation_history, attachments, conversation_id
        )

        return await self._llm_loop(messages, conversation_id, on_status)

    # ------------------------------------------------------------------
    # Message assembly
    # ------------------------------------------------------------------

    _DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

    def _build_messages(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None,
        attachments: list[dict[str, Any]] | None,
        conversation_id: str = "",
    ) -> list[dict[str, Any]]:
        # Inject session context so the LLM can pass user_id and
        # conversation_id to export tools.
        context_block = (
            f"\n\n## Session Context\n"
            f"- user_id: {self._DEFAULT_USER_ID}\n"
            f"- conversation_id: {conversation_id}\n"
            f"Use these values when calling export tools "
            f"(generate_csv, generate_kicad_library, generate_altium_library)."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT + context_block},
        ]

        # Append prior conversation turns
        if history:
            for h in history:
                role = h.get("role", "user")
                # History uses "message" key, OpenAI API needs "content"
                content = h.get("content") or h.get("message", "")
                if isinstance(content, dict):
                    content = content.get("message", "") or str(content)
                messages.append({"role": role, "content": content or "..."})

        # Build the current user turn -- may include image references
        content_parts: list[dict[str, Any]] = []

        if attachments:
            for att in attachments:
                if att.get("type") == "image" and att.get("base64"):
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{att['base64']}"
                            },
                        }
                    )
                elif att.get("type") == "text" and att.get("content"):
                    # PDF text extraction — include as text context
                    content_parts.append(
                        {
                            "type": "text",
                            "text": f"[Extracted PDF text with component values]\n{att['content']}",
                        }
                    )

        content_parts.append({"type": "text", "text": user_message})

        messages.append({"role": "user", "content": content_parts})
        return messages

    # ------------------------------------------------------------------
    # Core LLM + tool-call loop
    # ------------------------------------------------------------------

    async def _llm_loop(
        self,
        messages: list[dict[str, Any]],
        conversation_id: str,
        on_status: Callable[[str], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        """Iteratively call the LLM, dispatch tool calls, until a final answer."""
        max_iterations = 25  # safety limit to prevent infinite loops

        for iteration in range(max_iterations):
            log.info("llm_iteration", iteration=iteration, message_count=len(messages))

            response = await self._call_llm(messages)
            choice = response.choices[0]
            assistant_msg = choice.message

            # Append assistant message to conversation
            messages.append(assistant_msg.model_dump(exclude_none=True))

            # If no tool calls, the model has produced a final answer
            if not assistant_msg.tool_calls:
                return self._parse_final_answer(assistant_msg.content or "")

            # Process each tool call
            for tool_call in assistant_msg.tool_calls:
                fn = tool_call.function
                tool_name = fn.name
                try:
                    arguments = json.loads(fn.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                if on_status:
                    await on_status(f"Calling tool: {tool_name}")

                log.info(
                    "tool_call_dispatching",
                    tool=tool_name,
                    arguments=arguments,
                    iteration=iteration,
                )

                try:
                    result = await self._router.call_tool(tool_name, arguments)
                    result_str = (
                        json.dumps(result) if not isinstance(result, str) else result
                    )
                except Exception as exc:
                    log.error(
                        "tool_call_error",
                        tool=tool_name,
                        error=str(exc),
                        exc_info=True,
                    )
                    result_str = json.dumps(
                        {"error": str(exc), "tool": tool_name}
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_str,
                    }
                )

        # If we hit the iteration cap, return what we have
        log.warning("llm_loop_max_iterations", iterations=max_iterations)
        return {
            "status": "error",
            "message": "Agent reached maximum iteration limit without producing a final answer.",
            "data": {},
        }

    # ------------------------------------------------------------------
    # LLM call with exponential backoff
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: list[dict[str, Any]],
    ) -> Any:
        """Call the LLM with retry + exponential backoff on timeout."""
        last_exc: Exception | None = None

        for attempt in range(MAX_LLM_RETRIES):
            timeout = LLM_TIMEOUT_BASE * (2**attempt)
            try:
                log.info(
                    "llm_request",
                    attempt=attempt + 1,
                    timeout=timeout,
                    model=MODEL,
                )
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                    ),
                    timeout=timeout,
                )
                log.info(
                    "llm_response",
                    attempt=attempt + 1,
                    finish_reason=response.choices[0].finish_reason,
                )
                return response
            except asyncio.TimeoutError as exc:
                log.warning("llm_timeout", attempt=attempt + 1, timeout=timeout)
                last_exc = exc
            except Exception as exc:
                log.error("llm_error", attempt=attempt + 1, error=str(exc), exc_info=True)
                last_exc = exc
                # Don't retry on non-timeout errors
                break

        raise RuntimeError(
            f"LLM call failed after {MAX_LLM_RETRIES} attempts: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Parse final answer
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_final_answer(content: str) -> dict[str, Any]:
        """Try to parse the LLM's final answer as JSON."""
        content = content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last fence lines
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "status" in parsed:
                return parsed
            log.warning("parse_final_answer.missing_status", keys=list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__)
        except json.JSONDecodeError:
            log.warning("parse_final_answer.invalid_json", content_preview=content[:200])

        # If the LLM didn't return valid JSON, wrap it
        return {
            "status": "analysis",
            "message": content,
            "data": {},
        }
