.PHONY: install lint format test run

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest --cov --cov-branch --cov-report=term-missing

# Available from M2 onwards (app.api does not exist yet).
run:
	uv run uvicorn app.api:app --reload --port $${PORT:-8000}
