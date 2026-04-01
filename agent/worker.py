"""Redis-backed task worker.

Picks tasks from ``agent:tasks`` via BLMOVE, processes them through
:class:`AgentRunner`, and publishes status/result/error messages to
``agent:status:{conversation_id}``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import redis.asyncio as aioredis
import structlog

from agent_runner import AgentRunner

log = structlog.get_logger()

# Redis keys
QUEUE_TASKS = "agent:tasks"
QUEUE_PROCESSING = "agent:processing"
STATUS_PREFIX = "agent:status:"


class AgentWorker:
    """Async worker that consumes tasks from Redis and drives the agent."""

    def __init__(self, redis_url: str, max_concurrent: int = 50) -> None:
        self._redis_url = redis_url
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._redis: aioredis.Redis | None = None
        self._runner: AgentRunner | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the Redis connection and prepare the agent runner."""
        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        await self._redis.ping()
        log.info("redis_connected", url=self._redis_url)
        self._runner = AgentRunner()

    async def close(self) -> None:
        """Clean up connections."""
        if self._runner:
            await self._runner.close()
        if self._redis:
            await self._redis.aclose()
            log.info("redis_disconnected")

    # ------------------------------------------------------------------
    # Orphaned task recovery
    # ------------------------------------------------------------------

    async def requeue_orphaned_tasks(self) -> int:
        """Move any tasks stuck in ``agent:processing`` back to the queue.

        Returns the number of requeued tasks.
        """
        assert self._redis is not None
        count = 0
        while True:
            task_raw = await self._redis.rpoplpush(QUEUE_PROCESSING, QUEUE_TASKS)
            if task_raw is None:
                break
            count += 1
            # Try to publish a status update for the orphaned task
            try:
                task = json.loads(task_raw)
                conv_id = task.get("conversation_id", "unknown")
                task_id = task.get("task_id", "unknown")
                await self._publish(
                    conv_id,
                    {
                        "task_id": task_id,
                        "type": "status",
                        "text": "Requeued after worker restart",
                    },
                )
            except Exception:
                log.warning("requeue_status_publish_failed", exc_info=True)

        return count

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, shutdown_event: asyncio.Event | None = None) -> None:
        """Block-wait for tasks and process them concurrently.

        Stops when *shutdown_event* is set (graceful shutdown).
        """
        assert self._redis is not None
        log.info("worker_loop_started", max_concurrent=self._max_concurrent)

        tasks: set[asyncio.Task] = set()

        while True:
            # Check for shutdown
            if shutdown_event and shutdown_event.is_set():
                log.info("shutdown_requested_waiting_for_inflight_tasks", count=len(tasks))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                break

            # Wait for a task from the queue (1s timeout so we can check shutdown)
            try:
                task_raw = await self._redis.blmove(
                    QUEUE_TASKS,
                    QUEUE_PROCESSING,
                    timeout=1,
                    src="LEFT",
                    dest="RIGHT",
                )
            except Exception as exc:
                log.error("redis_blmove_error", error=str(exc), exc_info=True)
                await asyncio.sleep(1)
                continue

            if task_raw is None:
                # Timeout, no task available — loop back
                # Prune finished tasks
                tasks = {t for t in tasks if not t.done()}
                continue

            # Acquire semaphore slot before spawning
            await self._semaphore.acquire()

            async_task = asyncio.create_task(
                self._process_and_release(task_raw)
            )
            tasks.add(async_task)
            async_task.add_done_callback(tasks.discard)

    # ------------------------------------------------------------------
    # Task processing
    # ------------------------------------------------------------------

    async def _process_and_release(self, task_raw: str) -> None:
        """Process a task and release the semaphore slot when done."""
        try:
            await self.process_task(task_raw)
        finally:
            self._semaphore.release()

    async def process_task(self, task_raw: str) -> None:
        """Deserialize, run the agent, publish result, and clean up."""
        assert self._redis is not None
        assert self._runner is not None

        started_at = time.monotonic()
        task_id = "unknown"
        conversation_id = "unknown"

        try:
            task = json.loads(task_raw)
            task_id = task.get("task_id", str(uuid.uuid4()))
            conversation_id = task.get("conversation_id", "unknown")
            user_message = task.get("message", "")
            history = task.get("conversation_history", [])
            attachments = task.get("attachments", [])

            log.info(
                "task_started",
                task_id=task_id,
                conversation_id=conversation_id,
            )

            # Publish "processing" status
            await self._publish(
                conversation_id,
                {"task_id": task_id, "type": "status", "text": "Processing your request..."},
            )

            # Collect attachments from current message AND conversation history
            log.info("attachments_raw", current=len(attachments), history_len=len(history),
                     current_paths=[a.get("path","?") for a in attachments])
            all_attachments = list(attachments)
            for msg in history:
                msg_atts = msg.get("attachments", [])
                if msg_atts:
                    for att in msg_atts:
                        path = att.get("path", "")
                        if path and not any(a.get("path") == path for a in all_attachments):
                            all_attachments.append(att)

            # Fetch base64 for all image/PDF attachments
            log.info("all_attachments_collected", count=len(all_attachments),
                     paths=[a.get("path","?") for a in all_attachments])
            enriched_attachments = await self._prepare_attachments(
                all_attachments, conversation_id, task_id
            )

            # Status callback the runner can use
            async def _on_status(text: str) -> None:
                await self._publish(
                    conversation_id,
                    {"task_id": task_id, "type": "status", "text": text},
                )

            # Run the agent
            result = await self._runner.run(
                user_message=user_message,
                conversation_history=history,
                attachments=enriched_attachments,
                conversation_id=conversation_id,
                on_status=_on_status,
            )

            # If recommendation, generate exports deterministically
            if result.get("status") == "recommendation":
                await self._generate_exports(
                    result, conversation_id, task_id, _on_status
                )

            elapsed = time.monotonic() - started_at
            log.info(
                "task_completed",
                task_id=task_id,
                conversation_id=conversation_id,
                status=result.get("status"),
                elapsed_s=round(elapsed, 2),
            )

            # Publish result
            await self._publish(
                conversation_id,
                {"task_id": task_id, "type": "result", "data": result},
            )

        except Exception as exc:
            elapsed = time.monotonic() - started_at
            log.error(
                "task_failed",
                task_id=task_id,
                conversation_id=conversation_id,
                error=str(exc),
                elapsed_s=round(elapsed, 2),
                exc_info=True,
            )
            await self._publish(
                conversation_id,
                {"task_id": task_id, "type": "error", "error": str(exc)},
            )

        finally:
            # Remove from processing queue
            try:
                await self._redis.lrem(QUEUE_PROCESSING, 1, task_raw)
            except Exception:
                log.warning("lrem_processing_failed", task_id=task_id, exc_info=True)

    # ------------------------------------------------------------------
    # Attachment preparation
    # ------------------------------------------------------------------

    async def _prepare_attachments(
        self,
        attachments: list[dict[str, Any]],
        conversation_id: str,
        task_id: str,
    ) -> list[dict[str, Any]]:
        """Render PDFs and fetch base64 for all image attachments."""
        if not attachments:
            return []

        assert self._runner is not None
        router = self._runner._router
        enriched: list[dict[str, Any]] = []

        for att in attachments:
            path = att.get("path", "")
            # Infer type from extension if not explicitly set
            att_type = att.get("type", "")
            if not att_type and path:
                ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                if ext == "pdf":
                    att_type = "pdf"
                elif ext in ("png", "jpg", "jpeg", "webp"):
                    att_type = "image"

            if att_type == "pdf":
                # Two-phase PDF processing:
                # 1. Render all pages, extract text from ALL, build page index
                # 2. Include only top schematic images (max 3) + full text + page index
                # Agent can request more pages via get_image_base64 tool
                try:
                    minio_path = path if path.startswith("minio://") else f"minio://{path}"
                    raw_pages = await router.call_tool("render_pdf_pages", {"pdf_path": minio_path})
                    if isinstance(raw_pages, str):
                        raw_pages = json.loads(raw_pages)
                    if isinstance(raw_pages, dict) and "pages" in raw_pages:
                        page_list = raw_pages["pages"]
                    elif isinstance(raw_pages, list):
                        page_list = raw_pages
                    else:
                        log.warning("unexpected_render_result", result=type(raw_pages).__name__)
                        page_list = []

                    # Categorize pages
                    schematic_pages = []
                    all_page_index = []
                    for page in page_list:
                        page_path = page if isinstance(page, str) else page.get("minio_path", "")
                        classification = page.get("classification", "") if isinstance(page, dict) else "unknown"
                        page_num = page.get("number", 0) if isinstance(page, dict) else 0
                        if not page_path:
                            continue
                        all_page_index.append({
                            "page": page_num, "type": classification, "path": page_path
                        })
                        if classification != "text":
                            schematic_pages.append((page_path, page_num))

                    # Extract text from ALL pages (cheap, gives component values)
                    pdf_text_parts = []
                    for page_info in all_page_index:
                        pn = page_info["page"]
                        try:
                            text_result = await router.call_tool(
                                "extract_text", {"pdf_path": minio_path, "page_number": pn}
                            )
                            if isinstance(text_result, str):
                                try:
                                    parsed_text = json.loads(text_result)
                                    text_content = parsed_text.get("text", text_result)
                                except (json.JSONDecodeError, AttributeError):
                                    text_content = text_result
                                if text_content and len(text_content.strip()) > 30:
                                    preview = text_content.strip()[:200]
                                    pdf_text_parts.append(f"[Page {pn}] {text_content.strip()}")
                                    all_page_index[pn - 1]["text_preview"] = preview if pn <= len(all_page_index) else ""
                        except Exception:
                            pass

                    if pdf_text_parts:
                        combined_text = "\n\n".join(pdf_text_parts)
                        if len(combined_text) > 12000:
                            combined_text = combined_text[:12000] + "\n[...truncated]"
                        enriched.append({"type": "text", "content": combined_text})
                        log.info("pdf_text_extracted", pages=len(pdf_text_parts),
                                 chars=len(combined_text))

                    # Include max 2 schematic images, resized to save context
                    for page_path, page_num in schematic_pages[:2]:
                        try:
                            raw_b64 = await router.call_tool(
                                "get_image_base64", {"image_path": page_path}
                            )
                            if isinstance(raw_b64, str):
                                parsed_b64 = json.loads(raw_b64)
                                b64 = parsed_b64.get("base64", "")
                            else:
                                b64 = raw_b64
                            # Resize to max 1200px wide to stay within context limits
                            b64 = self._resize_base64(b64, max_width=1200)
                            enriched.append({"type": "image", "path": page_path, "base64": b64})
                        except Exception:
                            log.error("page_base64_failed", page=page_path, exc_info=True)

                    # Build page index so agent can request more via get_image_base64
                    if len(schematic_pages) > 2:
                        remaining = [f"  Page {pn}: {pp} (use get_image_base64 to load)"
                                     for pp, pn in schematic_pages[2:]]
                        enriched.append({
                            "type": "text",
                            "content": f"[Additional schematic pages available — not loaded to save context]\n"
                                       + "\n".join(remaining)
                        })

                    log.info("pdf_processed", total_pages=len(page_list),
                             schematics=len(schematic_pages),
                             images_included=min(3, len(schematic_pages)),
                             text_pages=len(pdf_text_parts))

                except Exception:
                    log.error("pdf_render_failed", path=path, exc_info=True)

            elif att_type == "image":
                try:
                    minio_path = path if path.startswith("minio://") else f"minio://{path}"
                    raw = await router.call_tool(
                        "get_image_base64", {"image_path": minio_path}
                    )
                    # MCP returns JSON string '{"base64": "..."}' — extract the actual data
                    if isinstance(raw, str):
                        parsed = json.loads(raw)
                        b64 = parsed.get("base64", "")
                    else:
                        b64 = raw
                    enriched.append({"type": "image", "path": path, "base64": b64})
                except Exception:
                    log.error("image_base64_failed", path=path, exc_info=True)

            else:
                enriched.append(att)

        return enriched

    # ------------------------------------------------------------------
    # Export generation (deterministic, not LLM-driven)
    # ------------------------------------------------------------------

    async def _generate_exports(
        self,
        result: dict[str, Any],
        conversation_id: str,
        task_id: str,
        on_status: Any,
    ) -> None:
        """Generate CSV/KiCad/Altium exports after a recommendation."""
        assert self._runner is not None
        router = self._runner._router
        data = result.get("data", {})
        components = data.get("components", [])
        if not components:
            return

        user_id = DEFAULT_USER_ID
        volume = data.get("bom_summary", {}).get("volume", 1)
        export_files: dict[str, str | None] = {"csv": None, "kicad_library": None, "altium_library": None}

        # CSV
        if on_status:
            await on_status("Generating CSV export...")
        try:
            csv_comps = [{"mpn": c.get("mpn", ""), "qty_per_unit": c.get("qty_per_unit", 1)} for c in components]
            csv_result = await router.call_tool("generate_csv", {
                "components": csv_comps, "volume": volume,
                "user_id": user_id, "conversation_id": conversation_id,
            })
            if isinstance(csv_result, str):
                csv_result = json.loads(csv_result)
            if isinstance(csv_result, dict):
                export_files["csv"] = csv_result.get("path")
            log.info("export_csv_ok", path=export_files["csv"])
        except Exception:
            log.error("export_csv_failed", exc_info=True)

        # KiCad
        if on_status:
            await on_status("Generating KiCad library...")
        try:
            kicad_comps = [{"mpn": c.get("mpn", ""), "description": c.get("description", "")} for c in components]
            kicad_result = await router.call_tool("generate_kicad_library", {
                "components": kicad_comps, "user_id": user_id, "conversation_id": conversation_id,
            })
            if isinstance(kicad_result, str):
                kicad_result = json.loads(kicad_result)
            if isinstance(kicad_result, dict):
                export_files["kicad_library"] = kicad_result.get("path")
            log.info("export_kicad_ok", path=export_files["kicad_library"])
        except Exception:
            log.error("export_kicad_failed", exc_info=True)

        # Altium
        if on_status:
            await on_status("Generating Altium library...")
        try:
            altium_comps = [{"mpn": c.get("mpn", ""), "description": c.get("description", "")} for c in components]
            altium_result = await router.call_tool("generate_altium_library", {
                "components": altium_comps, "user_id": user_id, "conversation_id": conversation_id,
            })
            if isinstance(altium_result, str):
                altium_result = json.loads(altium_result)
            if isinstance(altium_result, dict):
                export_files["altium_library"] = altium_result.get("path")
            log.info("export_altium_ok", path=export_files["altium_library"])
        except Exception:
            log.error("export_altium_failed", exc_info=True)

        # Inject export paths into result
        data["export_files"] = export_files

    # ------------------------------------------------------------------
    # Image resizing
    # ------------------------------------------------------------------

    @staticmethod
    def _resize_base64(b64: str, max_width: int = 1600) -> str:
        """Resize a base64 PNG/JPEG to max_width, return new base64."""
        if not b64:
            return b64
        try:
            import base64
            import io
            from PIL import Image

            img_bytes = base64.b64decode(b64)
            img = Image.open(io.BytesIO(img_bytes))

            if img.width <= max_width:
                return b64  # already small enough

            ratio = max_width / img.width
            new_h = int(img.height * ratio)
            img = img.resize((max_width, new_h), Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception:
            return b64  # return original on any error

    # ------------------------------------------------------------------
    # Redis pub/sub
    # ------------------------------------------------------------------

    async def _publish(self, conversation_id: str, payload: dict[str, Any]) -> None:
        """Publish a JSON message to ``agent:status:{conversation_id}``."""
        assert self._redis is not None
        channel = f"{STATUS_PREFIX}{conversation_id}"
        try:
            await self._redis.publish(channel, json.dumps(payload))
            log.debug(
                "status_published",
                channel=channel,
                type=payload.get("type"),
                task_id=payload.get("task_id"),
            )
        except Exception:
            log.error("publish_failed", channel=channel, exc_info=True)
