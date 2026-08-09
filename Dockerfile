# syntax=docker/dockerfile:1
# Minimal production image: Python 3.12 + uv in one slim layer.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install frozen dependencies first for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code.
COPY backend ./backend
COPY static ./static
COPY run.py ./

# Run as a non-root user.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV LM_CONSOLE_HOST=0.0.0.0 \
    LM_CONSOLE_PORT=8080

EXPOSE 8080

CMD [".venv/bin/python", "run.py"]