"""Web search client using LiteLLM for component lookups."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm-proxy:4000")
MODEL = "gpt-4o-mini"
REQUEST_TIMEOUT = 30.0

DISTRIBUTOR_SITES = [
    "mouser.com",
    "digikey.com",
    "lcsc.com",
    "tme.eu",
    "farnell.com",
]

SEARCH_SYSTEM_PROMPT = (
    "You are a helpful assistant that searches for electronic components on distributor websites. "
    "Return results as valid JSON only, no markdown fences, no explanation."
)

SEARCH_USER_PROMPT = """Search for electronic components matching: "{query}" site:{site}

Return a JSON object with this exact structure:
{{
  "results": [
    {{
      "mpn": "manufacturer part number",
      "manufacturer": "manufacturer name",
      "description": "short component description",
      "price": "unit price as string or null",
      "stock": "stock quantity as string or null",
      "url": "product page URL or null"
    }}
  ]
}}

Return up to 5 results. If no results are found, return {{"results": []}}.
Return ONLY the JSON object, nothing else."""

FETCH_SYSTEM_PROMPT = (
    "You are a helpful assistant that extracts structured product information from electronic component "
    "distributor pages. Return results as valid JSON only, no markdown fences, no explanation."
)

FETCH_USER_PROMPT = """Analyze the product page at this URL: {url}

Extract the following information and return as a JSON object:
{{
  "mpn": "manufacturer part number",
  "manufacturer": "manufacturer name",
  "description": "short component description",
  "price": "unit price as string or null",
  "stock": "stock quantity as string or null",
  "url": "{url}",
  "specs": {{}}
}}

Return ONLY the JSON object, nothing else. If you cannot determine a field, set it to null."""


def _build_chat_payload(
    system_prompt: str,
    user_prompt: str,
    use_web_search: bool = True,
) -> dict[str, Any]:
    """Build the chat completions request payload."""
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }
    if use_web_search:
        payload["tools"] = [{"type": "web_search"}]
    return payload


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse JSON from an LLM response, stripping markdown fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    return json.loads(cleaned)


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
    use_web_search: bool = True,
) -> dict[str, Any]:
    """Call LiteLLM and return parsed JSON response.

    Tries with web_search tool first. If the API rejects it (e.g. tool not
    supported), retries without the tool so the LLM answers from training data.
    """
    payload = _build_chat_payload(system_prompt, user_prompt, use_web_search)
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # If web_search tool is rejected, retry without it
            if use_web_search and exc.response.status_code in (400, 422):
                log.warning(
                    "web_search_not_available",
                    status=exc.response.status_code,
                    detail="Falling back to LLM training data",
                )
                payload = _build_chat_payload(system_prompt, user_prompt, use_web_search=False)
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            else:
                raise

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _parse_json_response(content)


async def search_distributor(query: str, site: str) -> dict[str, Any]:
    """Search a specific distributor site for components.

    Args:
        query: The component search query (e.g. "STM32F103C8T6").
        site: Distributor domain (e.g. "mouser.com").

    Returns:
        Dict with "results" list, each item tagged with mpn_confidence: "searched".
    """
    start = time.monotonic()
    bound_log = log.bind(tool="search_distributor", query=query, site=site)
    bound_log.info("search_started")

    try:
        user_prompt = SEARCH_USER_PROMPT.format(query=query, site=site)
        result = await _call_llm(SEARCH_SYSTEM_PROMPT, user_prompt)

        results = result.get("results", [])
        # Tag every result with mpn_confidence
        for item in results:
            item["mpn_confidence"] = "searched"

        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.info(
            "search_completed",
            result_count=len(results),
            duration_ms=duration_ms,
        )
        return {"results": results}

    except httpx.TimeoutException:
        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.error("search_timeout", duration_ms=duration_ms)
        return {"results": [], "error": "LLM request timed out"}

    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.error(
            "search_parse_error",
            error=str(exc),
            duration_ms=duration_ms,
        )
        return {"results": [], "error": f"Failed to parse LLM response: {exc}"}

    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.error(
            "search_error",
            error=str(exc),
            duration_ms=duration_ms,
        )
        return {"results": [], "error": str(exc)}


async def fetch_product_page(url: str) -> dict[str, Any]:
    """Fetch and extract structured product info from a distributor page URL.

    Args:
        url: Full URL to a component product page.

    Returns:
        Dict with product info, tagged with mpn_confidence: "searched".
    """
    start = time.monotonic()
    bound_log = log.bind(tool="fetch_product_page", url=url)
    bound_log.info("fetch_started")

    try:
        user_prompt = FETCH_USER_PROMPT.format(url=url)
        result = await _call_llm(FETCH_SYSTEM_PROMPT, user_prompt)

        result["mpn_confidence"] = "searched"

        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.info("fetch_completed", duration_ms=duration_ms)
        return result

    except httpx.TimeoutException:
        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.error("fetch_timeout", duration_ms=duration_ms)
        return {"error": "LLM request timed out", "url": url, "mpn_confidence": "searched"}

    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.error(
            "fetch_parse_error",
            error=str(exc),
            duration_ms=duration_ms,
        )
        return {
            "error": f"Failed to parse LLM response: {exc}",
            "url": url,
            "mpn_confidence": "searched",
        }

    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        bound_log.error(
            "fetch_error",
            error=str(exc),
            duration_ms=duration_ms,
        )
        return {"error": str(exc), "url": url, "mpn_confidence": "searched"}
