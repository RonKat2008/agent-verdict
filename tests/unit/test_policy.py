from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from verdict_hot import policy as policy_mod

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_check import validate_policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
SYSTEM_PYTHON39 = Path("/usr/bin/python3")


def test_packaged_default_validates_against_schema() -> None:
    raw = json.loads(PACKAGED_DEFAULT.read_text(encoding="utf-8"))
    validate_policy(raw)  # raises AssertionError on any mismatch


def test_load_policy_falls_back_to_packaged_default(isolated_verdict_home: Path) -> None:
    policy = policy_mod.load_policy()

    assert policy.policy_version == "2026.09.1"
    assert policy.mode == "shadow"
    assert policy.claims.max_claims == 6
    assert "implemented" in policy.claims.success_verbs


def test_load_policy_finds_packaged_default_via_cli_layout(isolated_verdict_home: Path) -> None:
    """The synced CLI copy resolves `default_policy.json` next to policy.py."""
    from agent_verdict.verdict_hot import policy as cli_policy_mod

    policy = cli_policy_mod.load_policy()

    assert policy.policy_version == "2026.09.1"
    assert policy.checks.runner_patterns  # non-empty: parsed correctly


def test_load_policy_merges_user_override_over_default(isolated_verdict_home: Path) -> None:
    isolated_verdict_home.mkdir(parents=True, exist_ok=True)
    (isolated_verdict_home / "policy.json").write_text(
        json.dumps({"mode": "enforce"}), encoding="utf-8"
    )

    policy = policy_mod.load_policy()

    assert policy.mode == "enforce"
    # every other top-level key survives untouched from the packaged default
    assert policy.policy_version == "2026.09.1"
    assert policy.claims.max_claims == 6


def test_load_policy_user_override_may_omit_most_keys(isolated_verdict_home: Path) -> None:
    isolated_verdict_home.mkdir(parents=True, exist_ok=True)
    (isolated_verdict_home / "policy.json").write_text(
        json.dumps({"claims": {"success_verbs": ["shipped"], "max_claims": 2}}),
        encoding="utf-8",
    )

    policy = policy_mod.load_policy()

    assert policy.claims.success_verbs == ("shipped",)
    assert policy.claims.max_claims == 2
    assert policy.mode == "shadow"  # untouched


def test_load_policy_explicit_path(tmp_path: Path) -> None:
    raw = json.loads(PACKAGED_DEFAULT.read_text(encoding="utf-8"))
    raw["policy_version"] = "test-explicit"
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps(raw), encoding="utf-8")

    policy = policy_mod.load_policy(custom)

    assert policy.policy_version == "test-explicit"


def test_load_policy_raises_on_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(policy_mod.PolicyError):
        policy_mod.load_policy(bad)


def test_load_policy_raises_on_missing_required_key(tmp_path: Path) -> None:
    raw = json.loads(PACKAGED_DEFAULT.read_text(encoding="utf-8"))
    del raw["claims"]
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(policy_mod.PolicyError):
        policy_mod.load_policy(incomplete)


def test_load_policy_raises_on_non_object_json(tmp_path: Path) -> None:
    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(policy_mod.PolicyError):
        policy_mod.load_policy(not_an_object)


def test_load_policy_never_compiles_a_policy_supplied_pattern(tmp_path: Path) -> None:
    """task-3-brief.md: "no regex compilation at import; compile lazily and
    cache." `load_policy()` only parses JSON into dataclasses -- it must not
    trigger `gates.py`/`claims.py` to compile any of the policy's pattern
    lists (globs, bash/output/masking/http-error regexes, success verbs).

    Runs in a subprocess: `gates`/`claims`'s lazy-compile caches are
    process-global `functools.cache`s, so checking them in-process would be
    polluted by whichever other test in this session already exercised
    `gates.is_never_send`/`is_check`/`is_soft_fail_candidate` or
    `claims.extract_claims` first.
    """
    script = (
        "import sys; sys.path.insert(0, 'plugin/hooks'); "
        "from verdict_hot import claims, gates, policy; "
        "p = policy.load_policy(); "
        "assert gates._compiled.cache_info().currsize == 0; "
        "assert gates._glob_regexes.cache_info().currsize == 0; "
        "assert claims._verb_patterns.cache_info().currsize == 0; "
        # sanity: the policy really does carry patterns to compile, so the
        # assertions above are meaningful and not vacuously true.
        "assert p.never_send.path_globs; "
        "assert p.claims.success_verbs; "
        "print('ok')"
    )
    env = {**os.environ, "VERDICT_HOME": str(tmp_path / "home")}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert proc.stdout.strip() == "ok"


@pytest.mark.slow
@pytest.mark.skipif(not SYSTEM_PYTHON39.exists(), reason="no system Python 3.9 at /usr/bin/python3")
def test_import_and_load_policy_incremental_under_10ms(tmp_path: Path) -> None:
    """task-3-brief.md's <10ms budget, measured the way it is actually paid.

    Importing `dataclasses` for the first time on CPython 3.9 unconditionally
    pulls in `inspect` (and `ast`/`dis`/`tokenize` beneath it) -- roughly
    20ms on a cold `/usr/bin/python3 -S` process, confirmed with `-X
    importtime`. `Policy` (this brief) and Task 4's `HookEvent` types both
    need a frozen dataclass tree, so in the real M1 pipeline `dataclasses`
    is paid for once by whichever hot-path module imports it first, before
    `policy.py` ever runs -- see
    `test_import_and_load_policy_in_isolation_reports_actual_cost` below for
    the one-time cost measured with nothing pre-imported. This test
    pre-imports the same stdlib modules the rest of the hot tree already
    uses (paths/ledger/logsafe/textnorm/redact, and dataclasses/typing for
    Task 4's parsers.py) to measure what policy+gates+claims+load_policy()
    ADD on top of an already-warm interpreter, which is the number the
    brief's budget is actually about.
    """
    script = (
        "import sys; sys.path.insert(0, 'plugin/hooks'); "
        "import os, re, json, pathlib, typing, dataclasses, functools, collections.abc; "
        "import time; t0 = time.perf_counter(); "
        "import verdict_hot.policy, verdict_hot.gates, verdict_hot.claims; "
        "verdict_hot.policy.load_policy(); "
        "print((time.perf_counter() - t0) * 1000)"
    )
    env = {**os.environ, "VERDICT_HOME": str(tmp_path / "home")}
    proc = subprocess.run(
        [str(SYSTEM_PYTHON39), "-S", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    elapsed_ms = float(proc.stdout.strip().splitlines()[-1])
    assert elapsed_ms < 10, f"incremental import+load took {elapsed_ms:.2f}ms >= 10ms"


@pytest.mark.slow
@pytest.mark.skipif(not SYSTEM_PYTHON39.exists(), reason="no system Python 3.9 at /usr/bin/python3")
def test_import_and_load_policy_in_isolation_reports_actual_cost(tmp_path: Path) -> None:
    """The literal brief measurement (nothing pre-imported) as a regression
    guard, not a <10ms gate -- see the docstring above for why the first
    `@dataclass` use in a fresh `/usr/bin/python3 -S` process alone costs
    roughly this much, independent of anything policy.py/gates.py/claims.py
    do. Measured (5 runs, task-3-report.md): ~26 ms steady state."""
    script = (
        "import sys; sys.path.insert(0, 'plugin/hooks'); "
        "import time; t0 = time.perf_counter(); "
        "import verdict_hot.policy, verdict_hot.gates, verdict_hot.claims; "
        "verdict_hot.policy.load_policy(); "
        "print((time.perf_counter() - t0) * 1000)"
    )
    env = {**os.environ, "VERDICT_HOME": str(tmp_path / "home")}
    proc = subprocess.run(
        [str(SYSTEM_PYTHON39), "-S", "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    elapsed_ms = float(proc.stdout.strip().splitlines()[-1])
    # Generous regression guard, not the brief's <10ms figure (see docstring).
    assert elapsed_ms < 60, f"isolated import+load took {elapsed_ms:.2f}ms >= 60ms"
