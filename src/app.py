"""
FastAPI application factory.

Creates and configures the FastAPI app with:
- Lifespan management (shared httpx.AsyncClient + BeetextingClient).
- API versioning (v1 router mounted at ``/api/v1``).
- Centralised error handlers.
- OpenAPI metadata.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from src.api.v1.router import router as v1_router
from src.core.config import get_settings
from src.core.exceptions import register_error_handlers
from src.core.logging_config import setup_logging
from src.services.beetexting_client import BeetextingClient
from src.services.queue_repo import QueueRepo
from src.services.queue_worker import sms_queue_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of long-lived resources.

    On startup:
        1. Configure logging.
        2. Create a shared httpx.AsyncClient (connection pooling).
        3. Wrap it in a BeetextingClient and stash both on app.state.
        4. Open the durable SQLite queue (recovers crashed in-flight rows).
        5. Start the single background SMS batch-queue worker task.

    On shutdown (ordered):
        1. Cancel the worker task and wait for it to stop.
        2. Close the SQLite queue connection.
        3. Close the shared httpx.AsyncClient.
    """
    settings = get_settings()
    setup_logging(level=settings.log_level, access_log_level=settings.access_log_level)

    logger.info("=== %s v%s starting ===", settings.app_name, settings.app_version)
    logger.info(
        "Config: token_service=%s, beetexting_send=%s, token_timeout=%.1fs, send_timeout=%.1fs",
        settings.token_service_url,
        settings.beetexting_send_url,
        settings.token_service_timeout_seconds,
        settings.beetexting_request_timeout_seconds,
    )

    http_client = httpx.AsyncClient()
    beetexting_client = BeetextingClient(settings=settings, http_client=http_client)

    app.state.http_client = http_client
    app.state.beetexting_client = beetexting_client

    queue_repo = QueueRepo(settings.sms_queue_db_path)
    await queue_repo.connect()
    app.state.queue_repo = queue_repo
    app.state.queue_worker_task = asyncio.create_task(
        sms_queue_worker(app), name="sms_queue_worker"
    )

    yield

    logger.info("=== %s shutting down ===", settings.app_name)
    worker_task = app.state.queue_worker_task
    worker_task.cancel()
    await asyncio.gather(worker_task, return_exceptions=True)
    await queue_repo.close()
    await http_client.aclose()


def create_app() -> FastAPI:
    """Build and return the fully-configured FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        summary="SMS sender for the NextGen Code Stroke Workflow.",
        description=(
            "Thin FastAPI microservice that sends outbound SMS messages via "
            "BEEtexting. Credentials are fetched on demand from the sibling "
            "beetexting_token_service, so this service holds no secrets of "
            "its own."
        ),
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )

    register_error_handlers(app)
    app.include_router(v1_router)

    # Prometheus metrics (§38)
    Instrumentator(
        excluded_handlers=[
            "/metrics",
            ".*/health.*",
            ".*/healthz",
            ".*/readyz",
            ".*/ping",
        ],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    return app
