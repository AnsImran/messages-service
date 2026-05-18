"""
SMS-domain Pydantic v2 schemas.

Models in this file cover the entire SMS request/response surface:

- ``SendSmsRequest``        — inbound payload to ``POST /api/v1/sms/send``.
- ``BeeTextingSendResponse``— validates the JSON returned by BEEtexting's
                              sendsms endpoint (upstream provider response).
- ``SendSmsResponse``       — outbound payload returned by this service to
                              its callers.
- ``TokenServiceResponse``  — validates the JSON returned by the sibling
                              beetexting_token_service ``/token`` endpoint.

Wire-format models that represent immutable upstream data are ``frozen``;
the inbound request model is mutable so FastAPI can populate it from JSON.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.config import get_settings

# E.164: leading '+', then 1–15 digits where the first cannot be 0.
E164_PATTERN = r"^\+[1-9]\d{1,14}$"


# ── Inbound request ─────────────────────────────────────────────────────────


class SendSmsRequest(BaseModel):
    """Body of a ``POST /api/v1/sms/send`` request.

    All three fields are required. Phone numbers must be in E.164 format
    (e.g. ``+19494248180``). The text body is capped at 1600 characters,
    which is the ceiling that BEEtexting carriers will split across SMS
    segments without truncation.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "from_number": "+19494248180",
                "to_number": "+19493137724",
                "text": "Hi Marko — automated test from microservice_sms, please ignore.",
            }
        }
    )

    from_number: str = Field(
        ...,
        pattern=E164_PATTERN,
        description=(
            "Sender phone number in E.164 format (e.g. +19494248180). "
            "Typically the main company number provisioned in BEEtexting."
        ),
        examples=["+19494248180"],
    )
    to_number: str = Field(
        ...,
        pattern=E164_PATTERN,
        description="Recipient phone number in E.164 format (e.g. +19493137724).",
        examples=["+19493137724"],
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=1600,
        description=(
            "The SMS body. BEEtexting accepts up to ~1600 characters; "
            "longer messages are split into multiple segments by the carrier."
        ),
        examples=["Hi Marko — automated test, please ignore."],
    )


# ── Upstream provider response ──────────────────────────────────────────────


class BeeTextingSendResponse(BaseModel):
    """Validates the JSON returned by BEEtexting's sendsms endpoint.

    ``extra='allow'`` keeps any extra fields BEEtexting may add later
    (e.g. message IDs) so we don't break on a benign upstream change.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        json_schema_extra={
            "example": {"result": "Message Processed Successfully"}
        },
    )

    result: str = Field(
        ...,
        min_length=1,
        description="Provider acknowledgement string returned by BEEtexting.",
        examples=["Message Processed Successfully"],
    )


# ── Outbound response from this service ─────────────────────────────────────


class SendSmsResponse(BaseModel):
    """Response returned by ``POST /api/v1/sms/send`` on success."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ok": True,
                "provider_response": {"result": "Message Processed Successfully"},
            }
        }
    )

    ok: bool = Field(
        default=True,
        description="True when BEEtexting accepted the message for delivery.",
        examples=[True],
    )
    provider_response: BeeTextingSendResponse = Field(
        ...,
        description="The raw acknowledgement returned by BEEtexting, validated.",
    )


# ── Token-service response ──────────────────────────────────────────────────


class TokenServiceResponse(BaseModel):
    """Validates the JSON returned by ``GET /api/v1/token`` on the sibling token service.

    Only the fields this service actually uses are pinned down; the rest
    (``expires_at_utc``, ``token_type``, ``ok``) are tolerated via
    ``extra='ignore'`` so token-service additions don't break us.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    access_token: str = Field(
        ...,
        min_length=1,
        repr=False,
        description="OAuth2 Bearer token issued by BEEtexting, used in the Authorization header.",
    )
    api_key: str = Field(
        ...,
        min_length=1,
        repr=False,
        description="The x-api-key value to send alongside the Bearer token on every BEEtexting call.",
    )


# ── Batch send (queued, multi-recipient) ────────────────────────────────────


class SendBatchRequest(BaseModel):
    """Body of a ``POST /api/v1/sms/batch`` request.

    Sends the *same* message (one ``from_number``, one ``text``) to many
    recipients. The request is validated, enqueued into the durable queue,
    and a ``batch_id`` is returned immediately — messages are then dispatched
    one at a time by the single paced worker, not during this request.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "from_number": "+19494248180",
                "to_numbers": ["+19493137724", "+19495551234"],
                "text": "Code Stroke activated — please respond.",
            }
        }
    )

    from_number: str = Field(
        ...,
        pattern=E164_PATTERN,
        description="Sender phone number in E.164 format (e.g. +19494248180).",
        examples=["+19494248180"],
    )
    to_numbers: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Recipient phone numbers in E.164 format. Duplicates are removed "
            "(first occurrence wins). Capped by SMS_MAX_RECIPIENTS_PER_BATCH."
        ),
        examples=[["+19493137724", "+19495551234"]],
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=1600,
        description=(
            "The SMS body sent to every recipient. BEEtexting accepts up to "
            "~1600 characters; longer messages are split by the carrier."
        ),
        examples=["Code Stroke activated — please respond."],
    )

    @field_validator("to_numbers")
    @classmethod
    def _validate_recipients(cls, value: list[str]) -> list[str]:
        # 1. Per-element E.164 validation (Field.pattern only validates scalars).
        for index, number in enumerate(value):
            if not re.fullmatch(E164_PATTERN, number):
                raise ValueError(
                    f"to_numbers[{index}] ('{number}') is not valid E.164 "
                    "(expected +<country><number>, e.g. +19493137724)"
                )
        # 2. De-duplicate, preserving first-seen order.
        deduped = list(dict.fromkeys(value))
        # 3. Enforce the per-batch cap before anything is enqueued.
        max_recipients = get_settings().sms_max_recipients_per_batch
        if len(deduped) > max_recipients:
            raise ValueError(
                f"too many recipients: {len(deduped)} exceeds the limit of "
                f"{max_recipients} per batch"
            )
        return deduped


class SendBatchResponse(BaseModel):
    """Response returned by ``POST /api/v1/sms/batch`` (HTTP 202)."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "ok": True,
                "batch_id": "3f9a1c7e8b2d4f6a9c0e1b2d3a4f5e6c",
                "accepted": 2,
                "status_url": "/api/v1/sms/status/3f9a1c7e8b2d4f6a9c0e1b2d3a4f5e6c",
            }
        },
    )

    ok: bool = Field(
        default=True,
        description="True when the batch was accepted and enqueued.",
        examples=[True],
    )
    batch_id: str = Field(
        ...,
        description="Opaque identifier — poll /api/v1/sms/status/{batch_id}.",
        examples=["3f9a1c7e8b2d4f6a9c0e1b2d3a4f5e6c"],
    )
    accepted: int = Field(
        ...,
        ge=1,
        description="Number of recipients enqueued (after de-duplication).",
        examples=[2],
    )
    status_url: str = Field(
        ...,
        description="Relative URL to poll for this batch's progress.",
        examples=["/api/v1/sms/status/3f9a1c7e8b2d4f6a9c0e1b2d3a4f5e6c"],
    )


class MessageStatus(BaseModel):
    """Per-recipient delivery state within a batch."""

    model_config = ConfigDict(frozen=True)

    to_number: str = Field(..., description="Recipient phone number (E.164).")
    status: Literal["pending", "sending", "sent", "failed"] = Field(
        ...,
        description=(
            "pending = queued/awaiting retry, sending = in flight, "
            "sent = accepted by BEEtexting, failed = exhausted retries or "
            "non-retryable error."
        ),
    )
    attempts: int = Field(
        ...,
        ge=0,
        description="Number of send attempts made so far.",
    )
    last_error: str | None = Field(
        default=None,
        description="Most recent error message, if any attempt failed.",
    )
    provider_result: str | None = Field(
        default=None,
        description="BEEtexting acknowledgement string once sent.",
    )


class BatchStatusResponse(BaseModel):
    """Response returned by ``GET /api/v1/sms/status/{batch_id}``."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "ok": True,
                "batch_id": "3f9a1c7e8b2d4f6a9c0e1b2d3a4f5e6c",
                "from_number": "+19494248180",
                "created_at_utc": "2026-05-18T14:23:45.123+00:00",
                "total": 2,
                "counts": {"sent": 1, "pending": 1},
                "batch_status": "in_progress",
                "messages": [
                    {
                        "to_number": "+19493137724",
                        "status": "sent",
                        "attempts": 1,
                        "last_error": None,
                        "provider_result": "Message Processed Successfully",
                    },
                    {
                        "to_number": "+19495551234",
                        "status": "pending",
                        "attempts": 0,
                        "last_error": None,
                        "provider_result": None,
                    },
                ],
            }
        },
    )

    ok: bool = Field(default=True, examples=[True])
    batch_id: str = Field(..., description="The batch identifier.")
    from_number: str = Field(..., description="Sender number used for the batch.")
    created_at_utc: str = Field(
        ...,
        description="ISO-8601 UTC timestamp when the batch was enqueued.",
    )
    total: int = Field(..., ge=1, description="Total recipients in the batch.")
    counts: dict[str, int] = Field(
        ...,
        description="Recipient count per message status.",
        examples=[{"sent": 1, "pending": 1}],
    )
    batch_status: Literal[
        "queued",
        "in_progress",
        "completed",
        "completed_with_failures",
        "failed",
    ] = Field(
        ...,
        description=(
            "queued = nothing attempted yet, in_progress = some work done, "
            "completed = all sent, failed = all failed, "
            "completed_with_failures = all terminal with a mix."
        ),
    )
    messages: list[MessageStatus] = Field(
        ...,
        description="Per-recipient status, in enqueue order.",
    )
