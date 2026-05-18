"""
The single SMS batch-queue worker.

One ``asyncio.Task`` started in the FastAPI lifespan. It drains the durable
queue strictly FIFO, one message at a time, sleeping a configurable gap **after
each message**. Because there is exactly one worker, that gap is the *global*
send pace across every concurrent ``/sms/batch`` request — producers only INSERT
rows; pacing is a property of the single consumer.

Retryable failures (token service unavailable, BEEtexting provider error,
unexpected exceptions) are rescheduled with exponential backoff via a persisted
``next_attempt_at_utc``; non-retryable errors fail immediately. The loop never
dies from one bad row — it logs and continues. Only cancellation (shutdown)
stops it.
"""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from pydantic import ValidationError

from src.core.config import Settings, get_settings
from src.core.exceptions import (
    BeetextingProviderError,
    SmsServiceError,
    TokenServiceUnavailableError,
)
from src.schemas.sms import SendSmsRequest
from src.services.beetexting_client import BeetextingClient
from src.services.queue_repo import ClaimedMessage, QueueRepo

logger = logging.getLogger(__name__)


def _backoff_delay(prior_attempts: int, settings: Settings) -> float:
    """Exponential backoff: min(cap, base * factor**prior_attempts) + jitter.

    ``prior_attempts`` is the number of attempts that already failed (0 on the
    first failure → ``base``).
    """
    raw = settings.sms_backoff_base_seconds * (
        settings.sms_backoff_factor**prior_attempts
    )
    delay = min(settings.sms_backoff_cap_seconds, raw)
    if settings.sms_backoff_jitter_seconds > 0:
        delay += random.uniform(0, settings.sms_backoff_jitter_seconds)
    return delay


async def _handle_failure(
    claimed: ClaimedMessage,
    repo: QueueRepo,
    settings: Settings,
    *,
    retryable: bool,
    error: str,
) -> None:
    attempts_after = claimed.attempts + 1
    if retryable and attempts_after < settings.sms_max_attempts:
        delay = _backoff_delay(claimed.attempts, settings)
        next_at = (
            datetime.now(UTC) + timedelta(seconds=delay)
        ).isoformat(timespec="microseconds")
        await repo.finalize_retry(claimed.id, next_at, error)
        logger.warning(
            "Batch %s: message id=%s to=%s attempt %d failed (%s); "
            "retrying in %.1fs",
            claimed.batch_id,
            claimed.id,
            claimed.to_number,
            attempts_after,
            error,
            delay,
        )
    else:
        await repo.finalize_failed(claimed.id, error)
        logger.error(
            "Batch %s: message id=%s to=%s permanently failed after "
            "%d attempt(s): %s",
            claimed.batch_id,
            claimed.id,
            claimed.to_number,
            attempts_after,
            error,
        )


async def _process_one(
    claimed: ClaimedMessage,
    repo: QueueRepo,
    client: BeetextingClient,
    settings: Settings,
) -> None:
    try:
        request = SendSmsRequest(
            from_number=claimed.from_number,
            to_number=claimed.to_number,
            text=claimed.text,
        )
    except ValidationError as exc:
        # Stored data should already be valid; if not, it never will be.
        await _handle_failure(
            claimed, repo, settings, retryable=False, error=str(exc)
        )
        return

    try:
        provider_response = await client.send_sms(request)
    except (TokenServiceUnavailableError, BeetextingProviderError) as exc:
        await _handle_failure(
            claimed, repo, settings, retryable=True, error=exc.message
        )
        return
    except SmsServiceError as exc:
        await _handle_failure(
            claimed, repo, settings, retryable=False, error=exc.message
        )
        return
    except Exception as exc:  # noqa: BLE001 — guard the worker, retry it
        logger.exception(
            "Unexpected error sending message id=%s", claimed.id
        )
        await _handle_failure(
            claimed, repo, settings, retryable=True, error=f"unexpected: {exc!r}"
        )
        return

    await repo.finalize_sent(claimed.id, provider_response.result)
    logger.info(
        "Batch %s: message id=%s to=%s sent on attempt %d (result=%r)",
        claimed.batch_id,
        claimed.id,
        claimed.to_number,
        claimed.attempts + 1,
        provider_response.result,
    )


async def sms_queue_worker(app: FastAPI) -> None:
    """Drain the SMS batch queue forever (until cancelled at shutdown)."""
    settings = get_settings()
    repo: QueueRepo = app.state.queue_repo
    client: BeetextingClient = app.state.beetexting_client

    logger.info(
        "SMS queue worker started (gap=%.2fs, max_attempts=%d, "
        "backoff=%.1fs x%.1f cap=%.1fs)",
        settings.sms_inter_message_gap_seconds,
        settings.sms_max_attempts,
        settings.sms_backoff_base_seconds,
        settings.sms_backoff_factor,
        settings.sms_backoff_cap_seconds,
    )

    while True:
        try:
            claimed = await repo.claim_next_due()
            if claimed is None:
                # Nothing due (empty queue or all in a backoff window).
                await asyncio.sleep(
                    settings.sms_worker_idle_poll_interval_seconds
                )
                continue

            await _process_one(claimed, repo, client, settings)

            # Global pacing gap — only after actually sending something.
            await asyncio.sleep(settings.sms_inter_message_gap_seconds)
        except asyncio.CancelledError:
            logger.info("SMS queue worker stopping (cancelled)")
            raise
        except Exception:  # noqa: BLE001 — never let the worker die
            logger.exception(
                "SMS queue worker iteration crashed; continuing"
            )
            await asyncio.sleep(
                settings.sms_worker_idle_poll_interval_seconds
            )
