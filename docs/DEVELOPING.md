# Developing agent-verdict

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) for dependency management and running project
  commands.
- Python 3.11+ for the CLI and tooling under `src/agent_verdict/` (`pyproject.toml`
  requires `>=3.11`).
- Apple's bundled `/usr/bin/python3` (3.9.6 on this project's development machine),
  used to prove the hook hot path under `plugin/hooks/` stays compatible with Python
  3.9+, since that is what runs on a machine with no other interpreter installed.

## Makefile targets

- `make setup`: `uv sync`, installs pre-commit hooks, runs `sync-hot`.
- `make check`: ruff check, ruff format check, `mypy --strict` on the default target and
  again with `--python-version 3.9` on `plugin/hooks`, a `diff -r` between
  `plugin/hooks/verdict_hot` and its mirror in `src/agent_verdict/verdict_hot` (the
  hot-tree sync gate), a diff between `plugin/policies/default.json` and its packaged
  copy, and a targeted pytest subset (import-ban, ledger, findings-ledger tests).
- `make test`: the full pytest suite.
- `make sync-hot`: runs `scripts/sync_hot.py`, which writes a verbatim copy of
  `plugin/hooks/verdict_hot/` into `src/agent_verdict/verdict_hot/`.
- `make gen-redact`: regenerates the redaction rule module and its synthetic corpus from
  the pinned `vendor/gitleaks.toml` snapshot.
- `make bench-hook`: runs `scripts/bench_hook.py`, gate G1.2 (hook latency).
- `make e2e-cheap`: runs `scripts/e2e_cheap.py`, gate G1.4 (a cheap real `claude -p`
  session that checks a failure is recorded correctly).
- `make plugin-validate`: `claude plugin validate ./plugin --strict`, gate G1.3.
- `make doctor`: runs `uv run verdict doctor` against your own environment.

## Running one hook by hand

```
VERDICT_HOME=$(mktemp -d) plugin/hooks/run.sh post-fail < tests/fixtures/hooks/post_tool_use_failure_bash.json; echo "exit=$?"
```

**Always redirect stdin explicitly**, as above. The launcher and the hook entry point
read stdin as the hook payload; run it without redirection (or with a heredoc left
open) and it will hang waiting on your terminal instead of failing fast. Point
`VERDICT_HOME` at a temp directory for any manual run so you never write test rows into
your real `~/.verdict`.

## Fixture capture workflow

`make capture-fixtures` runs `claude -p --plugin-dir ./plugin` in a temporary working
directory (using your real `HOME`, which is needed for authentication) over the tasks in
`scripts/capture_tasks.sh`, using a throwaway stub plugin (never the real one) that logs
every event's raw stdin. The raw payloads are then redacted and written to
`tests/fixtures/hooks/<event>_<tool>.json`, one file per event and tool variant, each
with a provenance line recorded in `tests/fixtures/hooks/PROVENANCE.md` (Claude Code
version and capture date). `make check` warns when a fixture's recorded version differs
from the Claude Code version currently installed. Freeze matchers and parsers against
these captured fixtures, not against hook documentation alone.

## The hot-tree sync rule

Everything under `plugin/hooks/verdict_hot/` is the source of truth for the hook hot
path. `src/agent_verdict/verdict_hot/` is a generated mirror, written by
`scripts/sync_hot.py` (`make sync-hot`). **Edit only the files under
`plugin/hooks/verdict_hot/`.** `make check` runs `diff -r` between the two trees and
fails if they differ, so an edit made directly in the `src/` mirror will be caught, not
silently kept.

## Gates G1.1 to G1.7

| Gate | What it checks | Command |
|---|---|---|
| G1.1 | `make check test` exits 0; the import-ban test proves no non-stdlib import under `plugin/hooks/` | `make check && make test` |
| G1.2 | Hook latency through the launcher. Originally 60 ms p50 / 120 ms p95; measured and revised by D-029 to 75 ms p50 / 150 ms p95, reported alongside the bare-interpreter floor | `make bench-hook` |
| G1.3 | The plugin manifest validates strictly | `make plugin-validate` |
| G1.4 | A cheap real session records a `post_fail` row with the right exit code and no hook-error text in the stream | `make e2e-cheap` |
| G1.5 | 16 parallel recorder processes, 500 rows, zero lost or interleaved rows | `uv run pytest tests/unit/test_ledger.py::test_16_processes_append_500_rows_without_interleaving` |
| G1.6 | `verdict doctor` exits 0 with zero provider keys configured, under the PATH interpreter and under `/usr/bin/python3` | `make doctor` |
| G1.7 | Redaction recall and false-positive rate on the corpus, and the sentinel-key test | `uv run pytest tests/test_redaction_gate.py` (also runs as part of `make test`) |

G1.7's exact bar was revised twice after real held-out probes (`docs/DECISIONS.md`
D-026, then D-027): overall recall at least 0.95 on a reviewer's fresh held-out set, at
least 0.90 of bare structured-family positives caught by a non-generic rule, an
**evidence-text** false-positive rate at most 0.02, and **opaque-token** over-redaction
reported (not gated), because a random-looking string with no word structure is
statistically indistinguishable from a real secret using text alone. Do not consider the
gate proven by the project's own corpus numbers alone; a fresh held-out probe from
someone other than the code's author is required evidence before trusting a change to
`redact.py`.

## Ledger row shape

The full set of fields per event type is defined in `schemas/ledger-v1.json`. Every
fixture row under `tests/fixtures/hooks/` is validated against this schema as part of
`make check`. See `docs/PRIVACY.md` for the same shape described in prose.

## Adding a policy pattern

Policy lives in `plugin/policies/default.json` (the packaged default) and is validated
against `schemas/policy-v1.json`. To add a new never-send path, a new soft-failure
pattern, or a new runner (check) pattern:

1. Add the pattern to the relevant list in `plugin/policies/default.json` (for example
   `never_send.path_globs`, `never_send.bash_patterns`, or the soft-failure or runner
   pattern lists).
2. Add a covering test that proves the pattern matches what it should and does not match
   an adjacent, similar-looking string it shouldn't.
3. Run `make check`, which diffs `plugin/policies/default.json` against its packaged
   copy in `src/agent_verdict/verdict_hot/default_policy.json` and fails if they
   disagree; keep both in sync the same way the hot-tree files are kept in sync.
4. Run `make test` to confirm the fixture and gate tests still pass.
