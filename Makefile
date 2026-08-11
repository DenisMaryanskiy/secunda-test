.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help install up down logs ps migrate revision lint fmt typecheck test test-unit audit check

help: ## Показать этот список
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Поставить зависимости и pre-commit хуки
	uv sync --all-groups
	uv run pre-commit install

up: ## Поднять всё окружение
	$(COMPOSE) up --build -d

down: ## Погасить окружение вместе с томами
	$(COMPOSE) down -v

logs: ## Логи прикладных сервисов
	$(COMPOSE) logs -f api consumer outbox-publisher

ps: ## Состояние сервисов
	$(COMPOSE) ps

migrate: ## Накатить миграции
	uv run alembic upgrade head

revision: ## Сгенерировать миграцию: make revision m="add something"
	uv run alembic revision --autogenerate -m "$(m)"

lint: ## Проверить стиль
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Отформатировать и починить, что чинится
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Проверить типы
	uv run mypy

test-unit: ## Быстрые тесты, без Docker
	uv run pytest -m "not integration"

test: ## Все тесты, включая интеграционные на testcontainers
	uv run pytest

audit: ## Проверить зависимости на известные уязвимости
	uv export --format requirements-txt --no-emit-project --all-groups \
		| uvx pip-audit --requirement /dev/stdin --disable-pip

check: lint typecheck test ## То же, что гоняет CI
