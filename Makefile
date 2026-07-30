.PHONY: sync lint format typecheck test check

sync:
	uv sync --all-groups

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy src

test:
	uv run pytest

check: lint typecheck test
	uv run ruff format --check .
