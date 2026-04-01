"""Thin wrapper around AsyncOpenAI with retry and JSON extraction."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog
from openai import AsyncOpenAI, APITimeoutError

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
    """Async OpenAI client with escalating timeout retry."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.reasoning_effort = reasoning_effort or os.environ.get(
            "OPENAI_REASONING_EFFORT"
        )

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = AsyncOpenAI(**client_kwargs)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        timeouts: list[int] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send a chat completion request with escalating timeout retry.

        Args:
            messages: Chat messages.
            tools: Optional tool definitions.
            timeouts: List of timeout values in seconds to try in order.
            **kwargs: Extra params forwarded to the API.

        Returns:
            The OpenAI ChatCompletion response.
        """
        timeouts = timeouts or PHASE3_TIMEOUTS

        base_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            **kwargs,
        }
        if tools:
            base_kwargs["tools"] = tools

        for attempt, timeout_secs in enumerate(timeouts):
            call_kwargs = {**base_kwargs, "timeout": timeout_secs}

            # Try with reasoning_effort if configured
            if self.reasoning_effort and "reasoning_effort" not in call_kwargs:
                call_kwargs["reasoning_effort"] = self.reasoning_effort

            try:
                return await self._client.chat.completions.create(**call_kwargs)
            except (TypeError, Exception) as exc:
                # If reasoning_effort is not supported, retry without it
                if "reasoning_effort" in call_kwargs and (
                    "unexpected keyword" in str(exc).lower()
                    or "unrecognized" in str(exc).lower()
                    or isinstance(exc, TypeError)
                ):
                    logger.warning(
                        "reasoning_effort not supported, retrying without",
                        attempt=attempt,
                    )
                    call_kwargs.pop("reasoning_effort", None)
                    self.reasoning_effort = None  # Don't try again
                    try:
                        return await self._client.chat.completions.create(
                            **call_kwargs
                        )
                    except APITimeoutError:
                        if attempt < len(timeouts) - 1:
                            logger.warning(
                                "timeout, escalating",
                                attempt=attempt,
                                timeout=timeout_secs,
                            )
                            continue
                        raise

                if isinstance(exc, APITimeoutError):
                    if attempt < len(timeouts) - 1:
                        logger.warning(
                            "timeout, escalating",
                            attempt=attempt,
                            timeout=timeout_secs,
                        )
                        continue
                    raise
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
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Build user message content
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for url in image_urls or []:
            content.append(
                {"type": "image_url", "image_url": {"url": url}}
            )

        messages.append({"role": "user", "content": content})

        response = await self.chat(
            messages, timeouts=PHASE2_TIMEOUTS, response_format={"type": "json_object"}
        )

        raw = response.choices[0].message.content or "{}"
        return _parse_json(raw)
