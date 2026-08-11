FROM ghcr.io/astral-sh/uv:0.9.27-python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Зависимости ставим до копирования кода, иначе правка исходников пересобирает их каждый раз.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --locked --no-dev

USER nobody

EXPOSE 8000
