"""
Health-check response schemas.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Response returned by ``GET /api/v1/health``."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "status": "healthy",
                "app_name": "microservice_sms",
                "app_version": "0.1.0",
                "token_service_reachable": True,
            }
        },
    )

    status: Literal["healthy", "degraded"] = Field(
        ...,
        description=(
            "Overall service status. 'healthy' when the token service is "
            "reachable; 'degraded' when it is not."
        ),
        examples=["healthy"],
    )
    app_name: str = Field(
        ...,
        description="Name of this service.",
        examples=["microservice_sms"],
    )
    app_version: str = Field(
        ...,
        description="Semantic version of this service.",
        examples=["0.1.0"],
    )
    token_service_reachable: bool = Field(
        ...,
        description=(
            "True if a probe call to the sibling token service succeeded "
            "during this health check."
        ),
        examples=[True],
    )
