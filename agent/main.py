"""Agent worker entrypoint.

Connects to Redis, requeues orphaned tasks from a prior crash,
then enters the blocking pick-process loop.
"""

import asyncio
import os
import signal
import sys

import structlog
from dotenv import load_dotenv

from worker import AgentWorker

load_dotenv()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()


async def main() -> None:
    redis_url = os.environ["REDIS_URL"]
    max_tasks = int(os.environ.get("AGENT_MAX_CONCURRENT_TASKS", "50"))

    log.info("agent_worker_starting", redis_url=redis_url, max_concurrent=max_tasks)

    worker = AgentWorker(redis_url=redis_url, max_concurrent=max_tasks)

    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("shutdown_signal_received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await worker.connect()

        # Requeue any orphaned tasks from a previous crash
        requeued = await worker.requeue_orphaned_tasks()
        if requeued:
            log.info("orphaned_tasks_requeued", count=requeued)

        # Run until told to stop
        await worker.run(shutdown_event)
    finally:
        await worker.close()
        log.info("agent_worker_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("fatal_error")
        sys.exit(1)
