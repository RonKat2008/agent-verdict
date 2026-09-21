.PHONY: setup check test test-fast capture-fixtures bench-provider clean

setup:
	uv sync
	uv run pre-commit install

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest tests/test_findings_ledger.py

test:
	uv run pytest

test-fast:
	uv run pytest -x -m "not slow"

capture-fixtures:
	sh scripts/capture_tasks.sh
	uv run python scripts/process_fixtures.py

bench-provider:
	uv run python scripts/smoke_jev.py --n $(or $(N),30) --provider openrouter --provider typesafe --json

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache dist
