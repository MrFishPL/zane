"""Thin wrapper around AsyncAnthropic with retry and JSON extraction."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog
from anthropic import AsyncAnthropic, APITimeoutError

logger = structlog.get_logger(__name__)

# Timeout sequences (seconds) for each phase
PHASE2_TIMEOUTS = [180, 360, 720]
PHASE3_TIMEOUTS = [60, 120, 240]

# Regex to strip markdown code fences from LLM JSON responses
_CODE_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL
)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON."""
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_json(text: str) -> dict[str, Any]:
    """Parse JSON from LLM output, stripping code fences if present."""
    cleaned = _strip_code_fences(text)
    return json.loads(cleaned)


class LLMClient:
    """Async Anthropic client with escalating timeout retry."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

        self._client = AsyncAnthropic(api_key=self.api_key)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        timeouts: list[int] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send a messages request with escalating timeout retry.

        Args:
            messages: Chat messages (system messages extracted automatically).
            tools: Optional tool definitions (OpenAI format converted to Anthropic format).
            timeouts: List of timeout values in seconds to try in order.
            **kwargs: Extra params forwarded to the API.

        Returns:
            The Anthropic Message response.
        """
        timeouts = timeouts or PHASE3_TIMEOUTS

        # Extract system message from messages list
        system_text = ""
        api_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"] if isinstance(msg["content"], str) else msg["content"]
            else:
                api_messages.append(msg)

        # Pop output_schema if provided — used for structured outputs
        output_schema = kwargs.pop("output_schema", None)

        base_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": kwargs.pop("max_tokens", 4096),
            "system": system_text,
            "messages": api_messages,
            **kwargs,
        }

        if output_schema:
            base_kwargs["output_format"] = {
                "type": "json_schema",
                "json_schema": output_schema,
            }

        if tools:
            # Convert OpenAI tool format to Anthropic format
            anthropic_tools = []
            for tool in tools:
                if tool.get("type") == "function":
                    fn = tool["function"]
                    anthropic_tools.append({
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                    })
                else:
                    anthropic_tools.append(tool)
            base_kwargs["tools"] = anthropic_tools

        for attempt, timeout_secs in enumerate(timeouts):
            call_kwargs = {**base_kwargs, "timeout": timeout_secs}

            try:
                if output_schema:
                    return await self._client.beta.messages.create(
                        betas=["structured-outputs-2025-11-13"],
                        **call_kwargs,
                    )
                return await self._client.messages.create(**call_kwargs)
            except APITimeoutError:
                if attempt < len(timeouts) - 1:
                    logger.warning(
                        "timeout, escalating",
                        attempt=attempt,
                        timeout=timeout_secs,
                    )
                    continue
                raise

        # Should not reach here, but just in case
        raise APITimeoutError(request=None)  # type: ignore[arg-type]

    async def analyze_schematic(
        self,
        system_prompt: str,
        user_text: str,
        image_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Analyze a schematic using vision + text (Phase 2).

        Args:
            system_prompt: The system prompt for schematic analysis.
            user_text: User's message / extracted text.
            image_urls: List of base64 data URIs or URLs for schematic images.

        Returns:
            Parsed JSON dict from the LLM response.
        """
        # Build user message content with Anthropic image format
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for url in image_urls or []:
            if url.startswith("data:"):
                # Parse data URI: data:image/jpeg;base64,<data>
                header, b64_data = url.split(",", 1)
                media_type = header.split(":")[1].split(";")[0]
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                })
            else:
                content.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })

        messages = [{"role": "user", "content": content}]

        response = await self.chat(
            [{"role": "system", "content": system_prompt}] + messages,
            timeouts=PHASE2_TIMEOUTS,
            max_tokens=8192,
        )

        # Extract text from response content blocks
        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw = block.text
                break
        return _parse_json(raw or "{}")
