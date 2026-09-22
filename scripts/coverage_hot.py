"""G2.1 coverage gate: `verdict_hot` line coverage, merged across its two
identical trees (task-7-brief.md controller notes ruling 2, PLAN.md G2.1).

`plugin/hooks/verdict_hot/` is the source of truth and `src/agent_verdict/
verdict_hot/` is its byte-identical mirror (`make sync-hot`; `make check`'s
`diff -r` enforces this on every commit). Most unit tests import the bare
`verdict_hot` package (`plugin/hooks` is on `pythonpath`, pyproject.toml);
a handful of CLI-layer tests import `agent_verdict.verdict_hot` instead;
the integration tests that spawn `run.sh` as a subprocess exercise the
`plugin/hooks` copy in a child process pytest-cov never instruments at all.

Summing statement coverage across the two trees as if they were different
code would double-count every statement and never reach 90 percent, since
a typical test exercises only one of the two import paths for a given
line. Since the two trees are provably identical byte-for-byte, this
script instead unions the *executed* line numbers for each same-named file
across both copies before computing a percentage -- a line counts as
covered if either copy's test run reached it. This is "the combined
figure" controller notes ruling 2 asks for.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_THRESHOLD = 90.0
_TREES = ("plugin/hooks/verdict_hot", "src/agent_verdict/verdict_hot")


def _run_pytest_cov(json_path: Path) -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--cov=agent_verdict.verdict_hot",
        "--cov=verdict_hot",
        f"--cov-report=json:{json_path}",
        "--cov-report=",
        "-q",
        "tests/unit",
        "tests/integration",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, timeout=600, check=False)
    return proc.returncode


def _relative_name(file_path: str) -> str | None:
    """`"plugin/hooks/verdict_hot/policy.py"` -> `"policy.py"`; `None` for a
    path under neither tracked tree (coverage.json paths are POSIX-style,
    relative to the run's cwd, which is `ROOT` here)."""
    for tree in _TREES:
        prefix = tree + "/"
        if file_path.startswith(prefix):
            return file_path[len(prefix) :]
    return None


def _int_set(value: object) -> set[int]:
    return set(value) if isinstance(value, list) else set()


def _merge(files: dict[str, dict[str, object]]) -> dict[str, tuple[set[int], int]]:
    """name -> (union of executed line numbers, statement count)."""
    merged: dict[str, tuple[set[int], int]] = {}
    for file_path, entry in files.items():
        name = _relative_name(file_path)
        if name is None:
            continue
        executed = _int_set(entry["executed_lines"])
        missing = _int_set(entry["missing_lines"])
        excluded = _int_set(entry.get("excluded_lines", []))
        num_statements = len(executed | missing | excluded) - len(excluded)
        prior_executed, prior_statements = merged.get(name, (set(), num_statements))
        merged[name] = (prior_executed | executed, max(prior_statements, num_statements))
    return merged


def _report(merged: dict[str, tuple[set[int], int]]) -> float:
    total_executed = 0
    total_statements = 0
    print(f"{'Name':<45}{'Stmts':>8}{'Cover':>8}")
    print("-" * 61)
    for name in sorted(merged):
        executed, statements = merged[name]
        covered = min(len(executed), statements)
        pct = 100.0 if statements == 0 else (covered / statements) * 100.0
        total_executed += covered
        total_statements += statements
        print(f"{name:<45}{statements:>8}{pct:>7.0f}%")
    total_pct = 100.0 if total_statements == 0 else (total_executed / total_statements) * 100.0
    print("-" * 61)
    label = "TOTAL (merged plugin/hooks + src copies)"
    print(f"{label:<45}{total_statements:>8}{total_pct:>7.2f}%")
    return total_pct


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "coverage.json"
        test_rc = _run_pytest_cov(json_path)
        if not json_path.exists():
            print("coverage_hot: pytest did not produce a coverage report", file=sys.stderr)
            return 1
        data = json.loads(json_path.read_text(encoding="utf-8"))

    merged = _merge(data["files"])
    total_pct = _report(merged)

    if test_rc != 0:
        print(f"coverage_hot: pytest exited {test_rc}", file=sys.stderr)
        return test_rc
    if total_pct < _THRESHOLD:
        print(f"FAIL: merged verdict_hot coverage {total_pct:.2f}% < {_THRESHOLD:.0f}%")
        return 1
    print(f"PASS: merged verdict_hot coverage {total_pct:.2f}% >= {_THRESHOLD:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
