"""`verdict policy lint <file>` (task-6-brief.md ruling 4).

Validates a policy file the same way the hot path loads one: through
`verdict_hot.policy.load_policy`, which already enforces every required
key and value type `schemas/policy-v1.json` describes -- a hand-written
structural check with no `jsonschema` dependency (task-3-brief.md), so
reusing it here keeps the CLI dependency-free too rather than adding a
second schema walker that could drift from the one `load_policy` already
maintains. On top of that, this module checks the threshold and budget
*ranges* `load_policy` does not: every `t_*` in `[0, 1]`, `t_ack <=
t_ack_hi`, `stop.max_blocks_per_prompt <= stop.ceiling`,
`provider.deadline_s <= provider.budget_s`, `state.target_tokens <=
state.max_tokens`, `span.max_prompts >= 1`, and every `denylist` regex
compiles.

`lint()` returns the list of problem lines (empty means clean) so tests
can assert on it directly; `run()`/`main()` print one line per problem and
exit 1, or print `OK` and exit 0.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from agent_verdict.verdict_hot.policy import Policy, PolicyError, load_policy

_THRESHOLD_NAMES = ("t_ack_hi", "t_done", "t_ack", "t_check", "t_soft", "t_claim")


def _threshold_range_problems(policy: Policy) -> list[str]:
    problems = []
    for name in _THRESHOLD_NAMES:
        value = getattr(policy.thresholds, name)
        if not (0.0 <= value <= 1.0):
            problems.append(f"thresholds.{name} must be in [0, 1], got {value!r}")
    return problems


def _cross_field_problems(policy: Policy) -> list[str]:
    problems = []
    if policy.thresholds.t_ack > policy.thresholds.t_ack_hi:
        problems.append(
            f"thresholds.t_ack ({policy.thresholds.t_ack!r}) must be <= "
            f"thresholds.t_ack_hi ({policy.thresholds.t_ack_hi!r})"
        )
    if policy.stop.max_blocks_per_prompt > policy.stop.ceiling:
        problems.append(
            f"stop.max_blocks_per_prompt ({policy.stop.max_blocks_per_prompt!r}) must be <= "
            f"stop.ceiling ({policy.stop.ceiling!r})"
        )
    if policy.provider.deadline_s > policy.provider.budget_s:
        problems.append(
            f"provider.deadline_s ({policy.provider.deadline_s!r}) must be <= "
            f"provider.budget_s ({policy.provider.budget_s!r})"
        )
    if policy.state.target_tokens > policy.state.max_tokens:
        problems.append(
            f"state.target_tokens ({policy.state.target_tokens!r}) must be <= "
            f"state.max_tokens ({policy.state.max_tokens!r})"
        )
    if policy.span.max_prompts < 1:
        problems.append(f"span.max_prompts must be >= 1, got {policy.span.max_prompts!r}")
    return problems


def _denylist_problems(policy: Policy) -> list[str]:
    problems = []
    for rule_id, pattern in zip(policy.denylist_ids, policy.denylist, strict=True):
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(f"denylist[{rule_id}] does not compile: {exc}")
    return problems


def lint(path: Path) -> list[str]:
    """Validate the policy file at `path`. Returns the problem lines found."""
    try:
        policy = load_policy(path)
    except PolicyError as exc:
        return [str(exc)]
    return [
        *_threshold_range_problems(policy),
        *_cross_field_problems(policy),
        *_denylist_problems(policy),
    ]


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verdict policy lint", description="Validate a policy file", add_help=add_help
    )
    parser.add_argument("file", help="path to the policy JSON file to lint")
    return parser


def run(args: argparse.Namespace) -> int:
    problems = lint(Path(args.file))
    if not problems:
        print("OK")
        return 0
    for problem in problems:
        print(problem)
    return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


__all__ = ["lint", "build_arg_parser", "run", "main"]
