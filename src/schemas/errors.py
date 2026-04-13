"""
Error response schemas.

Defines the standard error envelope returned by every failed request.
All FastAPI error handlers in ``src/core/exceptions.py`` return JSON
matching this shape so consuming services can parse errors reliably.
"""

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Inner error object with HTTP code and human-readable message."""

    model_config = ConfigDict(frozen=True)

    code: int = Field(
        ...,
        description="HTTP status code of the failure.",
        examples=[503],
    )
    message: str = Field(
        ...,
        description="Human-readable explanation of what went wrong.",
        examples=["Token service is unavailable."],
    )


class ErrorResponse(BaseModel):
    """Standard error envelope returned on any failure."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "ok": False,
                "error": {"code": 503, "message": "Token service is unavailable."},
            }
        },
    )

    ok: bool = Field(
        default=False,
        description="Always False on failure responses.",
    )
    error: ErrorDetail = Field(
        ...,
        description="Structured error detail.",
    )
