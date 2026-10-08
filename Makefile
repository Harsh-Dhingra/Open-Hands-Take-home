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
	uv run coverage report --include=app/engine.py --fail-under=100

# DB_PATH (default ./tictactoe.db) selects the SQLite file.
run:
	uv run uvicorn --factory app.api:create_app --reload --port $${PORT:-8000}
