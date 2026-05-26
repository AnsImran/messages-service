"""
Application configuration loaded from environment variables.

Every tuneable parameter lives here — nothing is hard-coded in the service
logic. Uses Pydantic Settings so values are validated at startup and the
service fails fast on missing or malformed configuration.
"""

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration for the SMS microservice.

    Values are read from environment variables (or a `.env` file in the
    project root). Names are case-insensitive — `token_service_url` and
    `TOKEN_SERVICE_URL` are both accepted.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service identity ────────────────────────────────────────────────────
    app_name: str = Field(
        default="microservice_sms",
        description="Human-readable name of this service, used in logs and OpenAPI.",
    )
    app_version: str = Field(
        default="0.1.0",
        description="Semantic version of this service, surfaced in OpenAPI metadata.",
    )

    # ── FastAPI server settings ─────────────────────────────────────────────
    app_host: str = Field(
        default="127.0.0.1",
        description="Host to bind the FastAPI server to. Default is localhost only.",
    )
    app_port: int = Field(
        default=8200,
        ge=1024,
        le=65535,
        description="Port for the FastAPI server.",
    )

    # ── Logging ─────────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    access_log_level: str = Field(
        default="INFO",
        description=(
            "Logging level for uvicorn's HTTP access log. INFO shows every "
            "incoming request; WARNING hides them for quieter production logs."
        ),
    )

    # ── Sibling token service ───────────────────────────────────────────────
    token_service_url: str = Field(
        default="http://localhost:8100/api/v1/token",
        description=(
            "Full URL of the sibling beetexting_token_service `/token` endpoint. "
            "This service calls it to obtain a fresh Bearer token + API key for "
            "every outbound BEEtexting request."
        ),
    )
    token_service_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=60.0,
        description="HTTP timeout (seconds) for the call to the token service.",
    )

    # ── BEEtexting send-SMS endpoint ────────────────────────────────────────
    beetexting_send_url: str = Field(
        default="https://connect.beetexting.com/prod/message/sendsms",
        description="BEEtexting REST endpoint for sending an SMS.",
    )
    beetexting_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=60.0,
        description="HTTP timeout (seconds) for the call to BEEtexting's sendsms endpoint.",
    )

    # ── SMS batch queue ─────────────────────────────────────────────────────
    # The /sms/batch endpoint enqueues messages into a durable SQLite-backed
    # queue drained by a single background worker. /sms/send is unaffected by
    # any of these — it stays synchronous and never touches the queue.
    sms_queue_db_path: str = Field(
        default="/app/data/sms_queue.db",
        min_length=1,
        description=(
            "Filesystem path to the SQLite file backing the SMS batch queue. "
            "In Docker this lives on a named volume so queued + in-flight "
            "messages survive container restarts and CI redeploys. Locally, "
            "set it to e.g. ./data/sms_queue.db."
        ),
    )
    sms_inter_message_gap_seconds: float = Field(
        default=1.5,
        ge=0,
        le=60.0,
        description=(
            "Seconds the single queue worker sleeps after each message before "
            "sending the next. This is the global send pace across ALL "
            "concurrent batch requests (there is only one worker)."
        ),
    )
    sms_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Maximum total send attempts per recipient (initial try + "
            "retries) before the message is marked permanently failed."
        ),
    )
    sms_backoff_base_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60.0,
        description="Base delay for exponential retry backoff (seconds).",
    )
    sms_backoff_factor: float = Field(
        default=2.0,
        ge=1,
        le=10.0,
        description=(
            "Exponential growth factor for retry backoff: "
            "delay = base * factor**attempts."
        ),
    )
    sms_backoff_cap_seconds: float = Field(
        default=60.0,
        gt=0,
        le=3600.0,
        description="Upper bound on a single retry backoff delay (seconds).",
    )
    sms_backoff_jitter_seconds: float = Field(
        default=0.0,
        ge=0,
        le=30.0,
        description=(
            "Maximum random jitter added to each backoff delay (seconds). "
            "0 disables jitter."
        ),
    )
    sms_worker_idle_poll_interval_seconds: float = Field(
        default=1.0,
        gt=0,
        le=60.0,
        description=(
            "How long the worker sleeps when the queue has nothing due "
            "before polling again (seconds)."
        ),
    )
    sms_max_recipients_per_batch: int = Field(
        default=500,
        ge=1,
        le=10000,
        description=(
            "Hard cap on recipients in a single /sms/batch request "
            "(enforced after de-duplication, before any DB write)."
        ),
    )

    @field_validator("log_level", "access_log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        normalised = value.upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalised not in valid:
            raise ValueError(f"log level must be one of {valid}, got '{value}'")
        return normalised

    @model_validator(mode="after")
    def _check_backoff_bounds(self) -> "Settings":
        if self.sms_backoff_cap_seconds < self.sms_backoff_base_seconds:
            raise ValueError(
                "sms_backoff_cap_seconds "
                f"({self.sms_backoff_cap_seconds}) must be >= "
                f"sms_backoff_base_seconds ({self.sms_backoff_base_seconds})"
            )
        return self


# ── Module-level singleton ──────────────────────────────────────────────────
# Imported once at startup; every module reads from the same instance.

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton, creating it on first call."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
