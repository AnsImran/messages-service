"""
API v1 route definitions.

All endpoints live under ``/api/v1/``. The router is mounted by the
application factory in ``src/app.py``. Adding a v2 in the future = a new
``src/api/v2/router.py`` mounted on the same app, with no changes to v1.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from src.core.config import Settings, get_settings
from src.core.exceptions import TokenServiceUnavailableError
from src.schemas.errors import ErrorResponse
from src.schemas.health import HealthResponse
from src.schemas.sms import (
    BatchStatusResponse,
    SendBatchRequest,
    SendBatchResponse,
    SendSmsRequest,
    SendSmsResponse,
)
from src.services.beetexting_client import BeetextingClient
from src.services.queue_repo import QueueRepo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1"])


# ── Dependencies ────────────────────────────────────────────────────────────


def get_beetexting_client(request: Request) -> BeetextingClient:
    """Pull the singleton BeetextingClient off `app.state`.

    The instance is created during the FastAPI lifespan startup hook in
    ``src/app.py`` and reused for the lifetime of the process so the
    underlying ``httpx.AsyncClient`` benefits from connection pooling.
    """
    client: BeetextingClient | None = getattr(request.app.state, "beetexting_client", None)
    assert client is not None, "BeetextingClient not initialised on app.state"
    return client


def get_queue_repo(request: Request) -> QueueRepo:
    """Pull the singleton QueueRepo off `app.state`.

    Created during the FastAPI lifespan startup hook in ``src/app.py`` (one
    long-lived aiosqlite connection) and reused for the process lifetime.
    """
    repo: QueueRepo | None = getattr(request.app.state, "queue_repo", None)
    assert repo is not None, "QueueRepo not initialised on app.state"
    return repo


BeetextingClientDep = Annotated[BeetextingClient, Depends(get_beetexting_client)]
QueueRepoDep = Annotated[QueueRepo, Depends(get_queue_repo)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post(
    "/sms/send",
    response_model=SendSmsResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Request validation failed."},
        502: {"model": ErrorResponse, "description": "BEEtexting provider error."},
        503: {"model": ErrorResponse, "description": "Token service unavailable."},
    },
    summary="Send an SMS via BEEtexting",
    description=(
        "Sends a single SMS message via BEEtexting. The service fetches a "
        "fresh Bearer token + API key from the sibling token service for "
        "every call, then forwards the request to BEEtexting's sendsms API."
    ),
)
async def send_sms_endpoint(
    payload: SendSmsRequest,
    client: BeetextingClientDep,
) -> SendSmsResponse:
    provider_response = await client.send_sms(payload)
    return SendSmsResponse(provider_response=provider_response)


@router.post(
    "/sms/batch",
    response_model=SendBatchResponse,
    status_code=202,
    responses={
        422: {"model": ErrorResponse, "description": "Request validation failed."},
    },
    summary="Queue an SMS to many recipients (paced, retried, durable)",
    description=(
        "Enqueues the same message to one-or-many recipients into a durable, "
        "globally-paced queue and returns immediately with a batch_id. A "
        "single background worker dispatches messages one at a time with a "
        "configurable inter-message gap and retries transient failures with "
        "exponential backoff. Poll GET /api/v1/sms/status/{batch_id} for "
        "progress. This does NOT block on BEEtexting."
    ),
)
async def send_batch_endpoint(
    payload: SendBatchRequest,
    repo: QueueRepoDep,
) -> SendBatchResponse:
    batch_id, accepted = await repo.enqueue_batch(
        from_number=payload.from_number,
        text=payload.text,
        to_numbers=payload.to_numbers,
    )
    return SendBatchResponse(
        batch_id=batch_id,
        accepted=accepted,
        status_url=f"/api/v1/sms/status/{batch_id}",
    )


@router.get(
    "/sms/status/{batch_id}",
    response_model=BatchStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Batch not found."},
    },
    summary="Get the progress of a queued batch",
    description=(
        "Returns aggregate counts, a derived batch status, and per-recipient "
        "delivery state for a previously-accepted batch."
    ),
)
async def batch_status_endpoint(
    batch_id: str,
    repo: QueueRepoDep,
) -> BatchStatusResponse:
    status = await repo.get_batch_status(batch_id)
    return BatchStatusResponse(**status)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description=(
        "Probes the sibling token service. Returns 'healthy' if it is "
        "reachable, otherwise 'degraded' (still 200 — orchestrators decide "
        "what to do about it)."
    ),
)
async def health_check(
    request: Request,
    client: BeetextingClientDep,
    settings: SettingsDep,
) -> HealthResponse:
    token_service_reachable = True
    try:
        await client.fetch_credentials()
    except TokenServiceUnavailableError as exc:
        logger.warning("Health check: token service unreachable (%s)", exc.message)
        token_service_reachable = False

    worker_task = getattr(request.app.state, "queue_worker_task", None)
    queue_worker_running = worker_task is not None and not worker_task.done()
    if worker_task is not None and worker_task.done():
        # Surface the crash reason; the worker should never finish on its own.
        exc = worker_task.exception() if not worker_task.cancelled() else None
        logger.error("Health check: queue worker is not running (exc=%r)", exc)

    return HealthResponse(
        status="healthy"
        if token_service_reachable and queue_worker_running
        else "degraded",
        app_name=settings.app_name,
        app_version=settings.app_version,
        token_service_reachable=token_service_reachable,
        queue_worker_running=queue_worker_running,
    )


@router.get(
    "/ping",
    summary="Liveness probe",
    description="Returns a static pong — confirms the process is alive. No dependencies.",
)
async def ping() -> dict[str, str]:
    return {"ping": "pong"}
