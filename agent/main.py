"""Agent service entrypoint."""

import asyncio
import os
import signal

import structlog
from dotenv import load_dotenv

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
    from worker import AgentWorker

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    max_concurrent = int(os.environ.get("AGENT_MAX_CONCURRENT_TASKS", "50"))

    worker = AgentWorker(redis_url, max_concurrent)
    await worker.connect()

    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: (
            log.info("worker.shutdown_signal", signal=s),
            shutdown_event.set(),
        ))

    log.info("worker.starting", redis_url=redis_url, max_concurrent=max_concurrent)
    try:
        await worker.run(shutdown_event)
    finally:
        await worker.close()
        log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
