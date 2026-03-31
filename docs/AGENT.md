# Agent

## Overview

The agent is an Anthropic Agent SDK worker running in its own Docker container. It does not expose an HTTP API. Instead, it pulls tasks from a Redis queue, processes them by orchestrating LLM calls and MCP tool invocations, and publishes results back through Redis pub/sub.

The agent never executes code directly -- all operations go through MCP tool calls. It only thinks, plans, and invokes tools.

## Framework and model

- **Framework**: Anthropic Agent SDK (Python)
- **Model**: GPT-5.4 (accessed through LiteLLM Proxy at `litellm-proxy:4000`)
- **LiteLLM** translates requests from Anthropic SDK format to OpenAI API format

The agent is defined via markdown files with YAML frontmatter. Changing behavior means editing text files, not code.

## Agent file structure

```
agent/
  CLAUDE.md                          # Main system prompt, identity, general rules
  .claude/
    agents/                          # Subagent definitions
      schematic-analyzer.md          # Analyzes schematic images, identifies components
      component-sourcer.md           # Searches Nexar, compares prices/stock
      cad-checker.md                 # Checks SnapMagic availability for CAD models
      export-generator.md            # Generates CSV and KiCad/Altium library files
    rules/                           # Domain rules
      component-selection.md         # Rules for selecting components
      data-sources.md                # Available data sources and priorities
      output-format.md               # JSON output format specification
      error-handling.md              # Error handling procedures
```

## Subagents

The agent uses subagents for parallel execution:

| Subagent | Purpose | MCP servers used |
|----------|---------|------------------|
| schematic-analyzer | Visual analysis of schematic pages, component identification | mcp-documents |
| component-sourcer | Parallel component search across Nexar, price comparison | mcp-nexar, mcp-websearch |
| cad-checker | CAD model availability on SnapMagic for all BOM components | mcp-snapmagic |
| export-generator | CSV BOM and KiCad/Altium library generation | mcp-export |

Subagent parallelism patterns:
- **Schematic analysis**: one subagent per PDF page or functional block.
- **Component sourcing**: parallel search for multiple components at once.
- **CAD model checking**: runs after component selection, checks all components in batch.

## Available MCP tools

### mcp-nexar (port 8001)
- `search_parts(query)` -- descriptive search (e.g., "3 ohm resistor 0603"). Returns top 5 results.
- `search_mpn(mpn)` -- search by manufacturer part number.
- `multi_match(mpns)` -- batch lookup of multiple MPNs.
- `check_lifecycle(mpn)` -- lifecycle status: `active`, `nrnd`, `obsolete`, or `unknown`.
- `get_quota_status()` -- remaining Nexar API quota.

### mcp-snapmagic (port 8002)
- `check_cad_availability(mpn, format)` -- check if symbol/footprint exists on SnapMagic. Format: `"kicad"`, `"altium"`, `"eagle"`, or `"any"`.
- `check_cad_batch(mpns, format)` -- batch lookup for multiple MPNs.

### mcp-documents (port 8003)
- `render_pdf_pages(pdf_path)` -- render all PDF pages to PNG (300 DPI). Returns JSON manifest.
- `render_pdf_page(pdf_path, page_number)` -- render a single page.
- `classify_page(pdf_path, page_number)` -- classify page as `"schematic"` or `"text"`.
- `extract_text(pdf_path, page_number)` -- extract native text (not OCR).
- `get_image_base64(image_path)` -- download image from MinIO, return as base64 for LLM vision.
- `crop_zoom_image(image_path, x1_pct, y1_pct, x2_pct, y2_pct)` -- crop and upscale a region. Returns base64 + MinIO path.
- `annotate_image(image_path, rectangles)` -- draw labeled red rectangles on image. Saves to MinIO.
- `get_image_info(image_path)` -- get dimensions, format, file size.
- `list_temp_files()` -- list files in temp bucket.
- `cleanup_temp()` -- delete all temp files.

### mcp-websearch (port 8004)
- `search_distributor(query, site)` -- search a distributor site (mouser.com, digikey.com, lcsc.com, tme.eu, farnell.com).
- `fetch_product_page(url)` -- extract product info from a distributor page URL.

### mcp-export (port 8005)
- `generate_csv(components, volume, user_id, conversation_id)` -- generate CSV BOM (MPN + quantity columns).
- `generate_kicad_library(components, user_id, conversation_id)` -- generate KiCad library ZIP (.kicad_sym + .kicad_mod).
- `generate_altium_library(components, user_id, conversation_id)` -- generate Altium library ZIP (.SchLib + .PcbLib).

## Response format

The agent always returns JSON with one of three statuses.

### Status: `needs_clarification`

Returned when the agent needs user input before proceeding.

```json
{
  "status": "needs_clarification",
  "message": "Plain text description of what is unclear",
  "data": {
    "questions": [
      {
        "id": 1,
        "question": "Is the 50 ohm value near the output a resistor or output impedance?",
        "default": "Output impedance"
      }
    ],
    "annotated_image": "minio://temp/{user_id}/{conversation_id}/annotated_page3.png"
  }
}
```

### Status: `analysis`

Intermediate schematic analysis -- what the agent recognized before sourcing.

```json
{
  "status": "analysis",
  "message": "Plain text description of recognized blocks and components",
  "data": {
    "blocks": [
      {
        "name": "Power supply section",
        "components": ["LM317", "2x 470uF", "2x 100nF"],
        "page": 1
      }
    ],
    "identified_components": ["LM317", "CD4069", "BC547", "1N4148"],
    "unclear_areas": [
      {
        "page": 2,
        "description": "Unreadable resistor value near Q1",
        "annotated_image": "minio://temp/.../annotated_page2.png"
      }
    ]
  }
}
```

### Status: `recommendation`

Full BOM with pricing, stock, alternatives, and download links.

```json
{
  "status": "recommendation",
  "message": "Plain text summary of what was found and why",
  "data": {
    "components": [
      {
        "ref": "U1",
        "mpn": "LM317T",
        "manufacturer": "STMicroelectronics",
        "description": "Adjustable voltage regulator, 1.2-37V, 1.5A, TO-220",
        "package": "TO-220",
        "qty_per_unit": 1,
        "qty_total": 100,
        "justification": "Widely available, lowest price at volume",
        "unit_price": 2.02,
        "price_break": {"qty": 100, "unit_price": 1.12},
        "stock": 24723,
        "lifecycle": "Active",
        "distributor": "Mouser",
        "distributor_url": "https://...",
        "datasheet_url": "https://...",
        "snapmagic_url": "https://www.snapeda.com/parts/...",
        "snapmagic_available": true,
        "snapmagic_formats": ["KiCad", "Altium", "Eagle"],
        "mpn_confidence": "verified",
        "verified": true,
        "warnings": [],
        "alternatives": [
          {
            "mpn": "LM317TG",
            "manufacturer": "onsemi",
            "unit_price": 2.78,
            "note": "Pin-compatible alternative"
          }
        ]
      }
    ],
    "not_sourced": [
      {"item": "Custom transformer", "reason": "No standard part found"}
    ],
    "bom_summary": {
      "unique_parts": 8,
      "total_components_per_unit": 10,
      "cost_per_unit": 33.56,
      "cost_total": 3356.00,
      "volume": 100,
      "currency": "USD"
    },
    "export_files": {
      "csv": "minio://exports/.../bom_2025-01-15.csv",
      "kicad_library": "minio://exports/.../kicad_library_2025-01-15.zip",
      "altium_library": "minio://exports/.../altium_library_2025-01-15.zip"
    },
    "sources_queried": ["Nexar/Octopart"]
  }
}
```

### Format rules

- `message` field: always plain text, never markdown.
- Null/empty fields: always present, use `null` for missing values, `[]` for empty arrays.
- Every component has an identical set of fields.
- Prices always in USD.
- `mpn_confidence`: `"verified"` (from Nexar API), `"searched"` (from web search), `"estimated"` (agent guess).

## Vision flow

How the agent "sees" schematics:

1. Agent calls `render_pdf_pages(pdf_path)` on mcp-documents. The server renders pages to PNG at 300 DPI, uploads to MinIO, returns a JSON manifest with page numbers, classifications, and MinIO paths.
2. Agent calls `get_image_base64(image_path)` on mcp-documents. The server fetches the image from MinIO and returns it as a base64 string. The agent does not access MinIO directly.
3. Agent sends the base64 image as an image attachment in the next LLM query (via LiteLLM). GPT-5.4 analyzes the image using computer vision (not OCR).
4. If a fragment is unreadable, the agent calls `crop_zoom_image(...)` which returns a cropped and upscaled region as base64, then repeats analysis on the zoomed version.
5. For annotated images sent back to the user (red rectangles marking unclear areas), the agent calls `annotate_image(...)` which saves to MinIO and returns the MinIO path. The frontend fetches and displays it via the backend's `/api/files/{path}` endpoint.

## Behavior modes

- **semi-manual** (default): agent shows proposals and waits for user approval before proceeding.
- **auto**: agent selects components autonomously. Requires explicit activation by the user.

## Selection priority

Default: lowest unit price. User can override to:
- Immediate availability (highest stock)
- Quality/reliability focus
- Specific distributor preference

## Context management

### What goes into conversation history

The backend prepares trimmed history before publishing to Redis. The agent never fetches raw history from Supabase.

- **User messages**: full text + attachment paths (no file contents or base64).
- **Recommendation responses**: `message` field + compressed BOM summary (`bom_compact` -- ref, MPN, description, package, qty only, approximately 40 tokens per component).
- **Other responses** (analysis, needs_clarification): `status` + `message` fields only.

### Truncation rules

- Maximum 20 most recent message pairs (user + assistant).
- Older messages are dropped (FIFO).
- Images and file contents are never included in history -- only MinIO paths.

## Error handling

- **MCP server unreachable**: agent informs the user which service is unavailable and what can still be done.
- **Nexar returns error or quota exceeded**: agent falls back to web search (mcp-websearch) and informs the user about reduced data quality.
- **LiteLLM timeout**: retry with exponential backoff, max 3 attempts.
- **Agent never returns empty**: always explains what happened and what it did to address the issue.

## Language behavior

The agent responds in the user's language. If the user writes in Polish, the agent responds in Polish. If the user writes in English, the agent responds in English.
