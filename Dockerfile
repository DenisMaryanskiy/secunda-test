FROM ghcr.io/astral-sh/uv:0.9.27-python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Базовый образ пересобирают реже, чем Debian выпускает security-патчи. Без этой
# строки в рантайм едут openssl и gnutls с известными CVE, а сервис ходит по TLS
# к чужим вебхукам. trivy следит в CI, что обновление не устарело.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Зависимости ставим до копирования кода, иначе правка исходников пересобирает их каждый раз.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --locked --no-dev

USER nobody

EXPOSE 8000
