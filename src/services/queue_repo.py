"""
Durable, SQLite-backed SMS batch queue.

This module owns the persistence layer for ``POST /api/v1/sms/batch``. The
"queue" is not a separate data structure — it is simply the set of ``messages``
rows whose ``status='pending'`` and whose ``next_attempt_at_utc`` is due. A
single background worker (see ``queue_worker.py``) claims one row at a time.

Concurrency model
-----------------
* One long-lived ``aiosqlite`` connection for the whole process. aiosqlite runs
  every operation for a connection on one dedicated thread, so this single
  connection is safe to share across the worker task and the API request
  coroutines without the stdlib ``check_same_thread`` footgun.
* The connection is opened in **autocommit** mode (``isolation_level=None``) so
  transaction boundaries are explicit and predictable.
* WAL journal mode lets status reads run concurrently with the worker's writes
  without blocking it.
* A single ``asyncio.Lock`` (owned by this repo) serializes *write*
  transactions. Status reads take no lock — WAL gives them a consistent
  snapshot, so polling never stalls the worker.

Durability
----------
The SQLite file lives on a Docker named volume, so queued and in-flight
messages survive container restarts and CI redeploys. On startup any row left
in the transient ``sending`` state (process died mid-send) is reset to
``pending`` and retried — at-least-once delivery, which for stroke alerting is
strictly preferable to silently dropping a message.
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from src.core.exceptions import BatchNotFoundError

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """UTC ISO-8601 with fixed microsecond precision.

    Fixed precision matters: ``next_attempt_at_utc`` is compared as a string
    (``<= :now``), so every timestamp must share an identical format for the
    lexicographic comparison to be correct.
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id        TEXT PRIMARY KEY,
    from_number     TEXT NOT NULL,
    text            TEXT NOT NULL,
    total           INTEGER NOT NULL,
    created_at_utc  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            TEXT NOT NULL REFERENCES batches(batch_id),
    to_number           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at_utc TEXT NOT NULL,
    last_error          TEXT,
    provider_result     TEXT,
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_messages_due
    ON messages (status, next_attempt_at_utc, id);
CREATE INDEX IF NOT EXISTS ix_messages_batch
    ON messages (batch_id);
"""

_TERMINAL = {"sent", "failed"}


@dataclass(frozen=True, slots=True)
class ClaimedMessage:
    """One unit of work handed to the worker by :meth:`QueueRepo.claim_next_due`."""

    id: int
    batch_id: str
    to_number: str
    from_number: str
    text: str
    attempts: int


class QueueRepo:
    """Persistence + claim/finalize operations for the SMS batch queue."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        # Serializes write transactions between the worker and API coroutines.
        self._write_lock = asyncio.Lock()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the connection, apply PRAGMAs, create schema, recover crashes.

        Raises whatever ``aiosqlite.connect`` raises (e.g. the directory is not
        writable) so the service fails fast and loudly at startup rather than
        accepting batches it can never persist.
        """
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        try:
            self._conn = await aiosqlite.connect(
                self._db_path, isolation_level=None
            )
        except Exception:
            logger.exception(
                "Failed to open SMS queue database at %s", self._db_path
            )
            raise

        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)

        recovered = await self._recover_in_flight()
        logger.info(
            "SMS queue ready at %s (recovered %d in-flight message(s))",
            self._db_path,
            recovered,
        )

    async def close(self) -> None:
        """Close the connection. Caller must stop the worker first."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _recover_in_flight(self) -> int:
        """Reset any 'sending' row left by a crashed process back to 'pending'.

        ``attempts`` is intentionally NOT incremented — an interrupted send is
        not the recipient's or provider's fault.
        """
        assert self._conn is not None
        async with self._write_lock:
            cur = await self._conn.execute(
                "UPDATE messages SET status='pending', updated_at_utc=? "
                "WHERE status='sending'",
                (_utcnow_iso(),),
            )
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # ── Enqueue ─────────────────────────────────────────────────────────────

    async def enqueue_batch(
        self, from_number: str, text: str, to_numbers: list[str]
    ) -> tuple[str, int]:
        """Persist a batch + one immediately-due message row per recipient.

        Returns ``(batch_id, accepted)``. ``to_numbers`` is expected to be
        already validated and de-duplicated by the request schema.
        """
        assert self._conn is not None
        batch_id = uuid.uuid4().hex
        now = _utcnow_iso()

        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                await self._conn.execute(
                    "INSERT INTO batches "
                    "(batch_id, from_number, text, total, created_at_utc) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (batch_id, from_number, text, len(to_numbers), now),
                )
                await self._conn.executemany(
                    "INSERT INTO messages "
                    "(batch_id, to_number, status, attempts, "
                    " next_attempt_at_utc, created_at_utc, updated_at_utc) "
                    "VALUES (?, ?, 'pending', 0, ?, ?, ?)",
                    [(batch_id, num, now, now, now) for num in to_numbers],
                )
                await self._conn.execute("COMMIT")
            except Exception:
                await self._conn.execute("ROLLBACK")
                raise

        logger.info(
            "Batch %s enqueued: %d recipient(s) from=%s",
            batch_id,
            len(to_numbers),
            from_number,
        )
        return batch_id, len(to_numbers)

    # ── Claim / finalize (worker) ───────────────────────────────────────────

    async def claim_next_due(self) -> ClaimedMessage | None:
        """Atomically claim the oldest due pending message, marking it 'sending'.

        ``BEGIN IMMEDIATE`` makes the select-then-update one atomic write
        transaction. Returns ``None`` when nothing is due.
        """
        assert self._conn is not None
        now = _utcnow_iso()

        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await self._conn.execute(
                    "SELECT m.id, m.batch_id, m.to_number, m.attempts, "
                    "       b.from_number, b.text "
                    "FROM messages m "
                    "JOIN batches b ON b.batch_id = m.batch_id "
                    "WHERE m.status = 'pending' "
                    "  AND m.next_attempt_at_utc <= ? "
                    "ORDER BY m.id "
                    "LIMIT 1",
                    (now,),
                )
                row = await cur.fetchone()
                if row is None:
                    await self._conn.execute("ROLLBACK")
                    return None

                await self._conn.execute(
                    "UPDATE messages SET status='sending', updated_at_utc=? "
                    "WHERE id=?",
                    (now, row["id"]),
                )
                await self._conn.execute("COMMIT")
            except Exception:
                await self._conn.execute("ROLLBACK")
                raise

        return ClaimedMessage(
            id=row["id"],
            batch_id=row["batch_id"],
            to_number=row["to_number"],
            from_number=row["from_number"],
            text=row["text"],
            attempts=row["attempts"],
        )

    async def finalize_sent(self, message_id: int, provider_result: str) -> None:
        """Mark a message delivered (BEEtexting accepted it)."""
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE messages SET status='sent', provider_result=?, "
                "attempts=attempts+1, last_error=NULL, updated_at_utc=? "
                "WHERE id=?",
                (provider_result, _utcnow_iso(), message_id),
            )

    async def finalize_retry(
        self, message_id: int, next_attempt_at_utc: str, last_error: str
    ) -> None:
        """Re-queue a message for a later retry after a backoff window."""
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE messages SET status='pending', attempts=attempts+1, "
                "next_attempt_at_utc=?, last_error=?, updated_at_utc=? "
                "WHERE id=?",
                (next_attempt_at_utc, last_error, _utcnow_iso(), message_id),
            )

    async def finalize_failed(self, message_id: int, last_error: str) -> None:
        """Mark a message permanently failed (retries exhausted / non-retryable)."""
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(
                "UPDATE messages SET status='failed', attempts=attempts+1, "
                "last_error=?, updated_at_utc=? WHERE id=?",
                (last_error, _utcnow_iso(), message_id),
            )

    # ── Status (read-only, no lock) ─────────────────────────────────────────

    async def get_batch_status(self, batch_id: str) -> dict:
        """Return aggregate + per-recipient status for a batch.

        Raises:
            BatchNotFoundError: if ``batch_id`` is unknown.
        """
        assert self._conn is not None

        cur = await self._conn.execute(
            "SELECT from_number, created_at_utc, total "
            "FROM batches WHERE batch_id=?",
            (batch_id,),
        )
        batch = await cur.fetchone()
        if batch is None:
            raise BatchNotFoundError(f"Batch '{batch_id}' not found.")

        cur = await self._conn.execute(
            "SELECT to_number, status, attempts, last_error, provider_result "
            "FROM messages WHERE batch_id=? ORDER BY id",
            (batch_id,),
        )
        rows = await cur.fetchall()

        messages = [
            {
                "to_number": r["to_number"],
                "status": r["status"],
                "attempts": r["attempts"],
                "last_error": r["last_error"],
                "provider_result": r["provider_result"],
            }
            for r in rows
        ]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        return {
            "batch_id": batch_id,
            "from_number": batch["from_number"],
            "created_at_utc": batch["created_at_utc"],
            "total": batch["total"],
            "counts": counts,
            "batch_status": self._derive_batch_status(rows),
            "messages": messages,
        }

    @staticmethod
    def _derive_batch_status(rows: list) -> str:
        statuses = [r["status"] for r in rows]
        if statuses and all(s == "sent" for s in statuses):
            return "completed"
        if statuses and all(s == "failed" for s in statuses):
            return "failed"
        if statuses and all(s in _TERMINAL for s in statuses):
            return "completed_with_failures"
        if all(
            r["status"] == "pending" and r["attempts"] == 0 for r in rows
        ):
            return "queued"
        return "in_progress"
