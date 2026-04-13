"""
Application entrypoint.

Run with:
    uv run python main.py          # uses settings from .env
    uv run uvicorn main:app        # same, via uvicorn CLI
"""

import uvicorn

from src.app import create_app
from src.core.config import get_settings

# Module-level app object so ``uvicorn main:app`` works.
app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
