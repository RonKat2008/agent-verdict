.PHONY: setup check test test-fast sync-hot gen-redact capture-fixtures bench-provider clean

setup:
	uv sync
	uv run pre-commit install
	$(MAKE) sync-hot

sync-hot:
	uv run python scripts/sync_hot.py

gen-redact:
	uv run python scripts/gen_redact.py
	uv run python scripts/gen_redact.py --corpus

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run mypy --python-version 3.9 plugin/hooks
	diff -r --exclude=__pycache__ plugin/hooks/verdict_hot src/agent_verdict/verdict_hot
	uv run pytest tests/test_findings_ledger.py tests/test_import_ban.py tests/unit/test_ledger.py

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
