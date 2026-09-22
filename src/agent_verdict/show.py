"""`verdict show <session_id> [--prompt <id>]` (task-6-brief.md ruling 5).

Prints one line per ledger row that carries user-facing evidence for a
session: `prompt` (excerpt only), `post`/`post_fail` (a numbered step with
tool, status, exit code, and a sanitized 80-character command), `verdict`
(question key and its numeric answer to 2 decimals), and `action` (the
decision, `would_have`, rule, and gate reason). It never prints
`out_head`/`out_tail`/`error_excerpt` content and never prints a prompt
beyond its stored excerpt -- those fields are simply never read here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping

from agent_verdict._render import excerpt, fmt_noul
from agent_verdict.verdict_hot import ledger, paths

_STEP_EVENTS = ("post", "post_fail")


def _line_for_prompt(row: Mapping[str, object]) -> str:
    pid = row.get("prompt_id")
    return f"prompt {pid}: {excerpt(row.get('prompt_excerpt'))}"


def _line_for_step(row: Mapping[str, object], seq: int) -> str:
    tool = row.get("tool_name")
    status = row.get("status")
    exit_code = row.get("exit_code")
    command = excerpt(row.get("input_excerpt"))
    return f"step {seq} {tool} {status} exit={exit_code} cmd={command}"


def _line_for_verdict(row: Mapping[str, object]) -> str:
    key = row.get("question_key")
    answer = row.get("answer")
    noul = answer.get("noul") if isinstance(answer, Mapping) else None
    return f"verdict {key}={fmt_noul(noul)}"


def _line_for_action(row: Mapping[str, object]) -> str:
    action = row.get("action")
    would_have = row.get("would_have")
    rule_id = row.get("rule_id")
    gate_reason = row.get("gate_reason")
    return (
        f"action action={action} would_have={would_have} rule={rule_id} gate_reason={gate_reason}"
    )


def _matches_prompt(row: Mapping[str, object], prompt_filter: str | None) -> bool:
    return prompt_filter is None or row.get("prompt_id") == prompt_filter


def _render_timeline(rows: list[dict[str, object]], prompt_filter: str | None) -> list[str]:
    lines: list[str] = []
    seq = 0
    for row in rows:
        if not _matches_prompt(row, prompt_filter):
            continue
        event = row.get("event")
        if event == "prompt":
            lines.append(_line_for_prompt(row))
        elif event in _STEP_EVENTS:
            seq += 1
            lines.append(_line_for_step(row, seq))
        elif event == "verdict":
            lines.append(_line_for_verdict(row))
        elif event == "action":
            lines.append(_line_for_action(row))
    return lines


def build_arg_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verdict show", description="Show one session's timeline", add_help=add_help
    )
    parser.add_argument("session_id", help="the session id to display")
    parser.add_argument("--prompt", dest="prompt_id", default=None, help="narrow to one prompt_id")
    return parser


def run(args: argparse.Namespace) -> int:
    session_id = args.session_id
    if not paths.session_file(session_id).exists():
        print(f"no such session: {session_id}", file=sys.stderr)
        return 1

    rows = ledger.read_session(session_id)
    for line in _render_timeline(rows, args.prompt_id):
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


__all__ = ["build_arg_parser", "run", "main"]
