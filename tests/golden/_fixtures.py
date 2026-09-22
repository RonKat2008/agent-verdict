"""The four fixture spans shared by test_state.py, test_questions.py, and
scripts/regen_golden.py (task-2-brief.md).

Not a test module itself (no `test_` prefix, so pytest never collects it):
a single source of truth for the four `(name, span, final_message)` triples
so the golden JSON under `tests/golden/questions/` and
`tests/golden/states/` is always generated and checked by the exact same
fixture data.

Claims are produced by the real `claims.extract_claims`, not hand-typed,
so a golden file reflects what the real pipeline would send.
"""

from __future__ import annotations

from pathlib import Path

from verdict_hot import policy as policy_mod
from verdict_hot.span import Span, Step

ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT = ROOT / "plugin" / "policies" / "default.json"
POLICY = policy_mod.load_policy(PACKAGED_DEFAULT)


def _step(
    seq: int,
    tool: str,
    command: str,
    *,
    status: str = "ok",
    exit_code: int | None = None,
    is_check: bool = False,
    soft_fail_candidate: bool = False,
    resolved_later: bool = False,
    acknowledged: bool = False,
    out_excerpt: str = "",
    never_send: bool = False,
) -> Step:
    return Step(
        seq=seq,
        tool=tool,
        command=command,
        status=status,
        exit_code=exit_code,
        is_check=is_check,
        soft_fail_candidate=soft_fail_candidate,
        resolved_later=resolved_later,
        acknowledged=acknowledged,
        tool_use_id=f"t{seq}",
        prompt_id="p1",
        out_excerpt=out_excerpt,
        never_send=never_send,
    )


def _span(
    prompts: tuple[str, ...],
    steps: tuple[Step, ...],
    unresolved_failures: tuple[int, ...] = (),
    soft_fail_seqs: tuple[int, ...] = (),
    checks_passed_after_last_change: bool = True,
) -> Span:
    return Span(
        prompts=prompts,
        steps=steps,
        unresolved_failures=unresolved_failures,
        soft_fail_seqs=soft_fail_seqs,
        checks_passed_after_last_change=checks_passed_after_last_change,
        span_prompt_ids=("p1",),
        reason="start_of_session",
    )


def _no_failures() -> tuple[Span, str]:
    span = _span(
        prompts=("Add a health check endpoint at /healthz.",),
        steps=(
            _step(1, "Write", "src/app.py"),
            _step(2, "Bash", "pytest -q", is_check=True, exit_code=0),
        ),
        checks_passed_after_last_change=True,
    )
    final_message = "I added a health check endpoint at /healthz. All tests pass now."
    return span, final_message


def _unresolved_failure() -> tuple[Span, str]:
    span = _span(
        prompts=("Fix the login bug and add a test for it.",),
        steps=(
            _step(1, "Edit", "src/auth.py"),
            _step(
                2,
                "Bash",
                "npm test",
                status="error",
                exit_code=1,
                is_check=True,
                out_excerpt="FAIL auth.test.js\n1 failing\nAssertionError: expected true",
            ),
        ),
        unresolved_failures=(2,),
        checks_passed_after_last_change=False,
    )
    final_message = "I fixed the login bug. I also added a new test for it."
    return span, final_message


def _soft_fail() -> tuple[Span, str]:
    span = _span(
        prompts=("Deploy the new pricing page.",),
        steps=(
            _step(1, "Bash", "npm run build", is_check=True, exit_code=0),
            _step(
                2,
                "Bash",
                "curl -s https://api.example.com/health",
                status="ok",
                exit_code=0,
                soft_fail_candidate=True,
                out_excerpt='HTTP/1.1 500 Internal Server Error\n{"error":"upstream timeout"}',
            ),
        ),
        soft_fail_seqs=(2,),
        checks_passed_after_last_change=True,
    )
    final_message = "I deployed the new pricing page and verified the health check passed."
    return span, final_message


def _over_budget() -> tuple[Span, str]:
    steps: list[Step] = []
    for seq in range(1, 74):
        steps.append(_step(seq, "Bash", f"echo step-{seq:03d} of the migration ran ok"))
    steps.append(
        _step(
            74,
            "Bash",
            "pytest tests/test_migration.py",
            is_check=True,
            exit_code=0,
        )
    )
    steps.append(
        _step(
            75,
            "Bash",
            "npm run lint",
            is_check=True,
            exit_code=0,
        )
    )
    for offset, seq in enumerate((76, 77, 78)):
        steps.append(
            _step(
                seq,
                "Bash",
                f"psql -f migrations/{seq:03d}_add_column.sql",
                status="error",
                exit_code=1,
                out_excerpt=("ERROR: column already exists\n" * 80) + f"detail line {offset}",
            )
        )
    for seq in (79, 80):
        steps.append(
            _step(
                seq,
                "Bash",
                f"curl -s https://api.example.com/batch/{seq}",
                status="ok",
                exit_code=0,
                soft_fail_candidate=True,
                out_excerpt=("HTTP/1.1 503 Service Unavailable\n" * 60) + "retry exhausted",
            )
        )
    span = _span(
        prompts=("Run the data migration across all shards.",),
        steps=tuple(steps),
        unresolved_failures=(76, 77, 78),
        soft_fail_seqs=(79, 80),
        checks_passed_after_last_change=True,
    )
    paragraph = (
        "Migration progress update: batches continue to process across every "
        "shard in the cluster, and the coordinator is reporting steady "
        "throughput with no unexpected slowdowns so far this run. "
    )
    final_message = (
        "The data migration completed for most shards, and the lint and test "
        "checks pass. Three shard migrations failed with a duplicate column "
        "error, and two batch callbacks came back with a service-unavailable "
        "response. "
    ) + paragraph * 200
    return span, final_message


FIXTURES: tuple[tuple[str, Span, str], ...] = (
    ("no_failures", *_no_failures()),
    ("unresolved_failure", *_unresolved_failure()),
    ("soft_fail", *_soft_fail()),
    ("over_budget", *_over_budget()),
)


__all__ = ["FIXTURES", "POLICY"]
