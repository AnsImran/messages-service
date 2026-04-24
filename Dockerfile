# Production image for the SMS microservice (BEEtexting sender).
#
# Uses `uv sync --frozen` to install from pyproject.toml + uv.lock so dep
# drift is impossible. CMD is wrapped with `opentelemetry-instrument`;
# when OTEL_* env vars are set by docker-compose, traces ship via OTLP
# to the observability collector.

FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── Dependency layer ─────────────────────────────────────────────────────
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Application code ─────────────────────────────────────────────────────
COPY main.py ./
COPY src/ ./src/

EXPOSE 8200

# Bind 0.0.0.0 so Docker can route traffic to this container. Host-only
# port mapping happens in docker-compose.yml.
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8200/api/v1/ping', timeout=3).status == 200 else sys.exit(1)" \
    || exit 1

# opentelemetry-instrument wraps uvicorn. Inert when OTEL env vars absent.
CMD ["opentelemetry-instrument", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8200"]
