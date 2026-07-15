# syntax=docker/dockerfile:1

# --- builder: resolve and install dependencies with uv ----------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer, so editing app code doesn't invalidate the
# (much slower) dependency-resolution layer on rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Now the project itself.
COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# --- runtime: slim image, no build tooling, non-root -------------------------------
FROM python:3.12-slim-bookworm

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --no-log-init appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app /app
COPY --chown=appuser:appuser docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    APPROVAL_ENV=docker

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
