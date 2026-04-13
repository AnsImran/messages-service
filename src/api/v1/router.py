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
from src.schemas.sms import SendSmsRequest, SendSmsResponse
from src.services.beetexting_client import BeetextingClient

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


BeetextingClientDep = Annotated[BeetextingClient, Depends(get_beetexting_client)]
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
    client: BeetextingClientDep,
    settings: SettingsDep,
) -> HealthResponse:
    token_service_reachable = True
    try:
        await client.fetch_credentials()
    except TokenServiceUnavailableError as exc:
        logger.warning("Health check: token service unreachable (%s)", exc.message)
        token_service_reachable = False

    return HealthResponse(
        status="healthy" if token_service_reachable else "degraded",
        app_name=settings.app_name,
        app_version=settings.app_version,
        token_service_reachable=token_service_reachable,
    )


@router.get(
    "/ping",
    summary="Liveness probe",
    description="Returns a static pong — confirms the process is alive. No dependencies.",
)
async def ping() -> dict[str, str]:
    return {"ping": "pong"}
