"""
Centralised logging configuration.

Sets up a single, consistent logging format across the entire service.
All log records include a UTC timestamp so they can be correlated with
logs from sibling microservices (e.g. beetexting_token_service).
"""

import logging
import sys
from datetime import UTC, datetime


class _UTCFormatter(logging.Formatter):
    """Formatter that always emits UTC timestamps with timezone info."""

    converter = lambda *_args: datetime.now(UTC).timetuple()  # noqa: E731

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        utc_now = datetime.now(UTC)
        if datefmt:
            return utc_now.strftime(datefmt)
        return utc_now.isoformat(timespec="milliseconds")


def setup_logging(level: str = "INFO", access_log_level: str = "INFO") -> None:
    """Configure the root logger for the application.

    Args:
        level: Main application logging level (e.g. "INFO", "DEBUG").
        access_log_level: Level for uvicorn's HTTP access log.
    """
    formatter = _UTCFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(access_log_level)
