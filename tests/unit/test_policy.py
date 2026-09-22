from __future__ import annotations

import json
import os
import statistics
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
    assert policy.state.target_tokens == 4000
    assert policy.state.max_tokens == 16000
    assert policy.state.excerpt_head == 200
    assert policy.state.excerpt_tail == 600


def test_load_policy_packaged_defaults_pin_task_4_thresholds(
    isolated_verdict_home: Path,
) -> None:
    """Fix round 1 item 5: pin the exact values task-4-brief.md specifies."""
    policy = policy_mod.load_policy()

    assert policy.thresholds.t_done == 0.7
    assert policy.thresholds.t_ack == 0.3
    assert policy.thresholds.t_ack_hi == 0.7
    assert policy.thresholds.t_check == 0.7
    assert policy.thresholds.t_soft == 0.8
    assert policy.thresholds.t_claim == 0.25


def test_load_policy_packaged_defaults_pin_task_4_stop_section(
    isolated_verdict_home: Path,
) -> None:
    policy = policy_mod.load_policy()

    assert policy.stop.max_blocks_per_prompt == 1
    assert policy.stop.ceiling == 2
    assert policy.stop.always_verify is False
    assert policy.stop.subagent_block is False


def test_load_policy_packaged_defaults_pin_task_4_provider_section(
    isolated_verdict_home: Path,
) -> None:
    policy = policy_mod.load_policy()

    assert policy.provider.default == "openrouter"
    assert policy.provider.deadline_s == 1.8
    assert policy.provider.budget_s == 2.5
    assert policy.provider.retry_min_remaining_s == 1.2
    assert policy.provider.breaker_open_s == 600


def test_load_policy_user_override_of_stop_and_provider_merges(
    isolated_verdict_home: Path,
) -> None:
    """Fix round 1 item 5: a user override of `stop`/`provider` merges
    through `_merge_top_level` the same way every other section does."""
    isolated_verdict_home.mkdir(parents=True, exist_ok=True)
    (isolated_verdict_home / "policy.json").write_text(
        json.dumps(
            {
                "stop": {
                    "max_blocks_per_prompt": 3,
                    "ceiling": 3,
                    "always_verify": True,
                    "subagent_block": True,
                },
                "provider": {
                    "default": "typesafe",
                    "deadline_s": 1.0,
                    "budget_s": 2.0,
                    "retry_min_remaining_s": 0.5,
                    "breaker_open_s": 60,
                },
            }
        ),
        encoding="utf-8",
    )

    policy = policy_mod.load_policy()

    assert policy.stop.max_blocks_per_prompt == 3
    assert policy.stop.always_verify is True
    assert policy.provider.default == "typesafe"
    assert policy.provider.breaker_open_s == 60
    # untouched sections keep the packaged default
    assert policy.thresholds.t_done == 0.7
    assert policy.mode == "shadow"


def test_load_policy_user_override_may_omit_state_section(isolated_verdict_home: Path) -> None:
    """task-2-brief.md: a user override that never touches `state` keeps the
    packaged default's values (the top-level merge in `_merge_top_level`
    handles this the same way it does for every other section)."""
    isolated_verdict_home.mkdir(parents=True, exist_ok=True)
    (isolated_verdict_home / "policy.json").write_text(
        json.dumps({"mode": "enforce"}), encoding="utf-8"
    )

    policy = policy_mod.load_policy()

    assert policy.state.target_tokens == 4000
    assert policy.state.max_tokens == 16000


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


_TARGET_SCRIPT = (
    "import sys; sys.path.insert(0, 'plugin/hooks'); "
    "import time; t0 = time.perf_counter(); "
    "import verdict_hot.policy, verdict_hot.gates, verdict_hot.claims; "
    "verdict_hot.policy.load_policy(); "
    "print((time.perf_counter() - t0) * 1000)"
)
_BASELINE_SCRIPT = (
    "import time; t0 = time.perf_counter(); "
    "import json, os, re, sys; "
    "print((time.perf_counter() - t0) * 1000)"
)


def _median_subprocess_ms(script: str, tmp_home: Path, runs: int = 12) -> float:
    """Median wall time (ms) of `script` over `runs` fresh subprocesses.

    Each run is a brand-new `/usr/bin/python3 -S` process (cold: no shared
    import cache across runs), matching fix round 1 item 0's methodology.
    """
    env = {**os.environ, "VERDICT_HOME": str(tmp_home)}
    samples = []
    for _ in range(runs):
        proc = subprocess.run(
            [str(SYSTEM_PYTHON39), "-S", "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        samples.append(float(proc.stdout.strip().splitlines()[-1]))
    return statistics.median(samples)


@pytest.mark.slow
@pytest.mark.skipif(not SYSTEM_PYTHON39.exists(), reason="no system Python 3.9 at /usr/bin/python3")
def test_import_and_load_policy_delta_under_10ms(tmp_path: Path) -> None:
    """fix round 1 item 0 (D-028): re-measured after dropping `dataclasses`.

    D-028 replaced `Policy`'s frozen dataclass tree with `typing.NamedTuple`
    because `dataclasses` alone costs ~8ms per process on Python 3.9 (it
    pulls in `inspect`/`ast`/`dis`/`tokenize`) -- see the (now superseded)
    ~26ms isolated measurement in task-3-report.md's "Fix round 1" section.
    Methodology: median of 12 cold `/usr/bin/python3 -S` subprocess runs
    for `import verdict_hot.policy, gates, claims` + one `load_policy()`
    call, minus the median of 12 runs importing only `json, os, re, sys`
    (the stdlib `policy.py`/`gates.py`/`claims.py` themselves need) --
    isolates what this task's code adds over that stdlib floor.
    """
    tmp_home = tmp_path / "home"
    target_median = _median_subprocess_ms(_TARGET_SCRIPT, tmp_home)
    baseline_median = _median_subprocess_ms(_BASELINE_SCRIPT, tmp_home)
    delta_ms = target_median - baseline_median
    assert delta_ms < 10, (
        f"policy+gates+claims+load_policy() added {delta_ms:.2f}ms over the "
        f"json/os/re/sys baseline (target median {target_median:.2f}ms, "
        f"baseline median {baseline_median:.2f}ms) -- budget is <10ms"
    )
