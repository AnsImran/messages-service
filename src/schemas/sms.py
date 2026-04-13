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

from pydantic import BaseModel, ConfigDict, Field

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
