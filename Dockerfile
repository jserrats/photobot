# syntax=docker/dockerfile:1.7

# ---- build stage: resolve and install locked dependencies into a venv ----
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime stage: minimal image, non-root user ----
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/app/.venv/bin:$PATH" \
    DOWNLOAD_DIR=/files

RUN groupadd --system --gid 1000 photobot \
    && useradd --system --uid 1000 --gid photobot --no-create-home photobot \
    && mkdir -p /files \
    && chown photobot:photobot /files

COPY --from=builder --chown=photobot:photobot /app/.venv /app/.venv

USER photobot
WORKDIR /app
VOLUME ["/files"]

ENTRYPOINT ["photobot"]
