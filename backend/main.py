"""Zane Backend — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import structlog
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from routers import conversations, messages, upload, files
from services import supabase_client, minio_client, redis_client
from websocket.manager import websocket_endpoint

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------

load_dotenv()

# ---------------------------------------------------------------------------
# Background task: staging file cleanup
# ---------------------------------------------------------------------------

_cleanup_task: asyncio.Task | None = None


async def _staging_cleanup_loop() -> None:
    """Periodically delete staging files older than 24 hours."""
    while True:
        try:
            deleted = minio_client.cleanup_staging(max_age_hours=24)
            log.info("staging_cleanup.run", deleted=deleted)
        except Exception as exc:
            log.error("staging_cleanup.error", error=str(exc))
        await asyncio.sleep(3600)  # every 1 hour


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task

    log.info("startup.begin")

    # Initialise services
    supabase_client.init()
    minio_client.init()
    await redis_client.init()

    # Start background cleanup
    _cleanup_task = asyncio.create_task(_staging_cleanup_loop())

    log.info("startup.complete")

    yield

    # Shutdown
    log.info("shutdown.begin")

    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass

    await redis_client.close()

    log.info("shutdown.complete")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Zane Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(upload.router)
app.include_router(files.router)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/conversations/{conversation_id}")
async def ws_route(websocket: WebSocket, conversation_id: str):
    await websocket_endpoint(websocket, conversation_id)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
