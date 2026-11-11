.PHONY: install run test lint format typecheck check up down logs migrate migration create-admin

install:
	poetry install

run:
	poetry run uvicorn app.main:create_app --factory --reload

test:
	poetry run pytest

lint:
	poetry run ruff check .
	poetry run ruff format --check .

format:
	poetry run ruff check --fix .
	poetry run ruff format .

typecheck:
	poetry run mypy app tests

check: lint typecheck test

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

migrate:
	poetry run alembic upgrade head

# make migration m="create users table"
migration:
	poetry run alembic revision --autogenerate -m "$(m)"

# make create-admin email=you@example.com
create-admin:
	poetry run python -m app.cli create-admin "$(email)"
