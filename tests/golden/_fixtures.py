"""The five fixture spans shared by test_state.py, test_questions.py, and
scripts/regen_golden.py (task-2-brief.md, fix round 1 item 3).

Not a test module itself (no `test_` prefix, so pytest never collects it):
a single source of truth for the five `(name, span, final_message)` triples
so the golden JSON under `tests/golden/questions/` and
`tests/golden/states/` is always generated and checked by the exact same
fixture data.

Claims are produced by the real `claims.extract_claims`, not hand-typed,
so a golden file reflects what the real pipeline would send.

`over_budget`'s final message is bounded to 8,000 characters (fix round 1
item 3: `store.final_message_max_chars` in `plugin/policies/default.json`
-- the real limit the recorder applies before this module ever sees a
message), so the compression it exercises (stage 1: drop plain-ok steps;
stage 2: shrink excerpts) is one production can actually reach, rather
than relying on an unrealistically huge message to dominate the budget.
`overflow` is a separate fixture: enough protected (error/soft-fail) steps
with excerpts survive stages 1-3 to blow past the hard cap on their own,
so stage 4 (drop soft-fail candidates, oldest first) has to run and
`overflow` comes back `True` from real compression, not from a message
no real `Stop` hook would ever pass in.
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
    prefix = (
        "The data migration completed for most shards, and the lint and test "
        "checks pass. Three shard migrations failed with a duplicate column "
        "error, and two batch callbacks came back with a service-unavailable "
        "response. "
    )
    final_message = (prefix + paragraph * 200)[:8000]
    return span, final_message


def _overflow() -> tuple[Span, str]:
    """Enough error and soft-fail steps, each with a sizeable raw excerpt,
    that even after stage 2 shrinks every excerpt to `excerpt_head` +
    `excerpt_tail`, the state is still over `max_tokens` -- stage 4 (drop
    the oldest soft-fail candidates) has to run for real, so `overflow`
    comes back `True` from actual compression."""
    steps: list[Step] = []
    unresolved: list[int] = []
    soft_fail: list[int] = []
    seq = 1
    for _ in range(60):
        steps.append(
            _step(
                seq,
                "Bash",
                f"psql -f migrations/{seq:04d}_backfill.sql",
                status="error",
                exit_code=1,
                out_excerpt=("ERROR: deadlock detected\n" * 100) + f"detail {seq}",
            )
        )
        unresolved.append(seq)
        seq += 1
    for _ in range(60):
        steps.append(
            _step(
                seq,
                "Bash",
                f"curl -s https://api.example.com/shard/{seq}/status",
                status="ok",
                exit_code=0,
                soft_fail_candidate=True,
                out_excerpt=("HTTP/1.1 503 Service Unavailable\n" * 90) + f"detail {seq}",
            )
        )
        soft_fail.append(seq)
        seq += 1
    span = _span(
        prompts=("Backfill every shard and report which ones failed.",),
        steps=tuple(steps),
        unresolved_failures=tuple(unresolved),
        soft_fail_seqs=tuple(soft_fail),
        checks_passed_after_last_change=False,
    )
    prefix = (
        "The backfill completed for most shards, and the smoke test checks "
        "pass. Sixty shard backfills failed outright, and sixty status "
        "checks came back with a service-unavailable response. "
    )
    padding = "Every shard reports its own backfill lag and retry count. "
    final_message = (prefix + padding * 150)[:8000]
    return span, final_message


FIXTURES: tuple[tuple[str, Span, str], ...] = (
    ("no_failures", *_no_failures()),
    ("unresolved_failure", *_unresolved_failure()),
    ("soft_fail", *_soft_fail()),
    ("over_budget", *_over_budget()),
    ("overflow", *_overflow()),
)


__all__ = ["FIXTURES", "POLICY"]
