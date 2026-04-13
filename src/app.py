"""
FastAPI application factory.

Creates and configures the FastAPI app with:
- Lifespan management (shared httpx.AsyncClient + BeetextingClient).
- API versioning (v1 router mounted at ``/api/v1``).
- Centralised error handlers.
- OpenAPI metadata.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from src.api.v1.router import router as v1_router
from src.core.config import get_settings
from src.core.exceptions import register_error_handlers
from src.core.logging_config import setup_logging
from src.services.beetexting_client import BeetextingClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of long-lived resources.

    On startup:
        1. Configure logging.
        2. Create a shared httpx.AsyncClient (connection pooling).
        3. Wrap it in a BeetextingClient and stash both on app.state.

    On shutdown:
        1. Close the shared httpx.AsyncClient.
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

    yield

    logger.info("=== %s shutting down ===", settings.app_name)
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

    return app
