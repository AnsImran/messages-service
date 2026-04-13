"""
BEEtexting client.

Encapsulates everything related to talking to:

1. The sibling ``beetexting_token_service`` (to fetch a valid Bearer token
   and the matching API key).
2. BEEtexting's own sendsms endpoint (to actually deliver the message).

The client is instantiated once at startup with a long-lived
``httpx.AsyncClient`` that the FastAPI lifespan owns. Reusing one client
across requests gives us connection pooling and avoids the overhead of
spinning up a TLS connection per call.
"""

import logging

import httpx
from pydantic import ValidationError

from src.core.config import Settings
from src.core.exceptions import BeetextingProviderError, TokenServiceUnavailableError
from src.schemas.sms import (
    BeeTextingSendResponse,
    SendSmsRequest,
    TokenServiceResponse,
)

logger = logging.getLogger(__name__)


class BeetextingClient:
    """High-level client for sending SMS messages via BEEtexting."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client

    # ── Token service ───────────────────────────────────────────────────────

    async def fetch_credentials(self) -> TokenServiceResponse:
        """Fetch a fresh Bearer token + API key from the sibling token service.

        Raises:
            TokenServiceUnavailableError: on any network failure, non-2xx
                response, or unexpected JSON shape.
        """
        url = self._settings.token_service_url
        timeout = self._settings.token_service_timeout_seconds
        try:
            response = await self._http.get(url, timeout=timeout)
        except httpx.HTTPError as exc:
            logger.error("Token service request failed: %s", exc)
            raise TokenServiceUnavailableError(
                f"Could not reach token service at {url}: {exc}"
            ) from exc

        if response.status_code != 200:
            logger.error(
                "Token service returned %d: %s", response.status_code, response.text
            )
            raise TokenServiceUnavailableError(
                f"Token service responded with HTTP {response.status_code}."
            )

        try:
            return TokenServiceResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            logger.error("Token service returned malformed JSON: %s", exc)
            raise TokenServiceUnavailableError(
                "Token service returned an unexpected response shape."
            ) from exc

    # ── BEEtexting sendsms ──────────────────────────────────────────────────

    async def send_sms(self, payload: SendSmsRequest) -> BeeTextingSendResponse:
        """Send an SMS via BEEtexting and return the validated provider response.

        Raises:
            TokenServiceUnavailableError: if credentials can't be obtained.
            BeetextingProviderError:      if BEEtexting refuses the request.
        """
        credentials = await self.fetch_credentials()
        url = self._settings.beetexting_send_url
        timeout = self._settings.beetexting_request_timeout_seconds

        try:
            response = await self._http.post(
                url,
                headers={
                    "Authorization": f"Bearer {credentials.access_token}",
                    "x-api-key": credentials.api_key,
                },
                params={
                    "from": payload.from_number,
                    "to": payload.to_number,
                    "text": payload.text,
                },
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            logger.error("BEEtexting request failed: %s", exc)
            raise BeetextingProviderError(
                f"Could not reach BEEtexting at {url}: {exc}"
            ) from exc

        if response.status_code != 200:
            logger.error(
                "BEEtexting returned %d: %s", response.status_code, response.text
            )
            raise BeetextingProviderError(
                f"BEEtexting responded with HTTP {response.status_code}."
            )

        try:
            provider_response = BeeTextingSendResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            logger.error("BEEtexting returned malformed JSON: %s", exc)
            raise BeetextingProviderError(
                "BEEtexting returned an unexpected response shape."
            ) from exc

        logger.info(
            "SMS dispatched: from=%s to=%s result=%r",
            payload.from_number,
            payload.to_number,
            provider_response.result,
        )
        return provider_response
