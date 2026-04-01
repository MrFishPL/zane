"""Agent service entrypoint."""

import asyncio
import os
import signal

import structlog
from dotenv import load_dotenv

load_dotenv()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


async def main() -> None:
    from worker import AgentWorker

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    max_concurrent = int(os.environ.get("AGENT_MAX_CONCURRENT_TASKS", "50"))

    worker = AgentWorker(redis_url, max_concurrent)
    await worker.connect()

    shutdown_event = asyncio.Event()

    def handle_signal(sig, _frame):
        log.info("worker.shutdown_signal", signal=sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("worker.starting", redis_url=redis_url, max_concurrent=max_concurrent)
    try:
        await worker.run(shutdown_event)
    finally:
        await worker.close()
        log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
