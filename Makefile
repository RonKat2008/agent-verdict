.PHONY: setup check test test-fast sync-hot gen-redact regen-golden capture-fixtures coverage-hot bench-provider bench-hook e2e-cheap plugin-validate doctor clean

setup:
	uv sync
	uv run pre-commit install
	$(MAKE) sync-hot

sync-hot:
	uv run python scripts/sync_hot.py

gen-redact:
	uv run python scripts/gen_redact.py
	uv run python scripts/gen_redact.py --corpus

regen-golden:
	uv run python scripts/regen_golden.py

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run mypy --python-version 3.9 plugin/hooks
	diff -r --exclude=__pycache__ --exclude=default_policy.json plugin/hooks/verdict_hot src/agent_verdict/verdict_hot
	diff plugin/policies/default.json src/agent_verdict/verdict_hot/default_policy.json
	uv run pytest tests/test_findings_ledger.py tests/test_import_ban.py tests/unit/test_ledger.py

test:
	uv run pytest

test-fast:
	uv run pytest -x -m "not slow"

capture-fixtures:
	sh scripts/capture_tasks.sh
	uv run python scripts/process_fixtures.py

coverage-hot:
	uv run python scripts/coverage_hot.py

bench-provider:
	uv run python scripts/smoke_jev.py --n $(or $(N),30) --provider openrouter --provider typesafe --json

bench-hook:
	uv run python scripts/bench_hook.py --n $(or $(N),40)

e2e-cheap:
	uv run python scripts/e2e_cheap.py

plugin-validate:
	claude plugin validate ./plugin --strict

doctor:
	uv run verdict doctor

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache dist
