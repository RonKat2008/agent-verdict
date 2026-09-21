# CLAUDE.md: Verdict (`agent-verdict`)

A Claude Code plugin that catches an agent claiming success its own tool results do not support. It records tool outcomes (including exit codes) from lifecycle hooks, asks TypeSafe's Jev model one batch of typed questions about the final message at Stop time, and applies a versioned policy: pass, flag, or block the stop. It also ships the labeling and evaluation tools behind a published, reproducible calibration study.

**Status (2026-09-20): the owner confirmed `docs/PLAN.md` and every default open decision (D-022). Milestone M0 is built and reviewed. Gates G0.1 to G0.3 passed (G0.2 on 2026-09-21: OpenRouter p90 265 ms, D-025). G0.4 passed on 2026-09-21 (CI green on Linux and macOS) and M0 is tagged `m0`. M1, the collector, is in progress on branch `m1`. A development key lives outside the repo in `~/.config/agent-verdict/dev.env` (mode 0600); load it with `set -a; . ~/.config/agent-verdict/dev.env; set +a` and never copy it into the project.** Update this line whenever a milestone is tagged.

## Read first, in this order of authority

1. `docs/VERIFIED_FACTS.md`: platform and vendor facts checked against live sources. Highest authority.
2. `docs/DECISIONS.md`: architecture decisions D-001 onward. Fixed unless the owner agrees to change one.
3. `docs/PLAN.md`: the build plan. Section 7 has the milestones and their gate commands.
4. `docs/FINDINGS_LEDGER.md`: 97 plan-review findings plus the final document review, and what was done with each. Full text in `docs/research/`.
5. `docs/archive/VERDICT_PLAN.original.md`: the owner's original plan. **Historical only. It contains refuted assumptions. Never copy from it.**

## Rules of engagement

1. **Milestone by milestone.** Do not start M(n+1) until every gate command of M(n) exits 0. Show the owner the gate output, then tag `m<n>`.
2. **Tests first** for the ledger, parsers, gates, claim extractor, state builder, questions, policy, guard, rules, redactor, and eval metrics. Use the `superpowers:writing-plans` skill for each milestone's task plan and `superpowers:test-driven-development` while building.
3. **Citation rule.** Never state or code against an API field, CLI flag, or contract clause you have not read in a source during this session. Cite `file:line` or a fetched URL. If you cannot, write "unverified" and stop. A research worker on this project invented hook field names (`blockDecision`, `permissionDecision: "block"`); both were false.
4. **Fixtures beat documentation.** Parsers and matchers are frozen against captured real hook stdin in `tests/fixtures/hooks/`, each with a provenance line naming the Claude Code version. If the installed version differs, re-capture before trusting them.
5. **Never hardcode a threshold.** Every threshold is a named key in `plugin/policies/default.json` with a test.
6. **Ask before changing architecture.** Implementation details are yours. Component boundaries and decisions are not.
7. **Log decisions.** One `docs/DECISIONS.md` entry per non-obvious choice, with context, decision, reason, and what would reopen it.
8. **If it is not a command, it is not a gate or a metric.** Gates exit non-zero on failure. Every published number comes from `make eval` and a tagged report.

## Hot-path invariants (code under `plugin/hooks/`)

- **Standard library only, Python 3.9+.** No pydantic, SQLAlchemy, httpx, yaml, requests, or typesafe-sdk. The launcher runs Python with `-S` only and unsets `PYTHON*` variables. Never add `-E`: it costs about 68 ms on Apple's Python (D-019). A CI test bans other imports and any file that shadows a stdlib module name. The full planned stack cost 154 ms to import; stdlib costs about 25 to 38 ms.
- **Recorders are synchronous and never touch the network.** No `async: true` hooks. Target under 60 ms.
- **The model path fails open:** any exception, timeout, or provider error logs to `~/.verdict/hook.log` and exits 0. **The rules path fails closed:** an internal error in the PreToolUse rules exits 2.
- **Never emit `permissionDecision: "allow"`.** The pass case is exit 0 with empty stdout.
- **Facts are not model judgments.** A step failed because `PostToolUseFailure` fired. The model never overturns that.
- **Nothing Claude reads contains raw tool output.** Block reasons carry step number, tool, exit code, and a sanitized command only.
- **One `redact()`, three call sites:** provider request, ledger write, export. The never-send list runs before it. A redactor exception sends nothing.
- **Never hold a file lock across a network call.** One `write()` per ledger row.
- **The API key** is read from `CLAUDE_PLUGIN_OPTION_API_KEY` (or the provider env var), never from argv or disk, and never logged.
- **Always enforce an internal deadline** and return a decision. A timed-out hook is cancelled and silently fails open.
- `VERDICT_DISABLE=1` exits 0 before any other work. On Windows the hot path disables itself.

## Never reintroduce

| Wrong belief (from the original plan or a bad worker) | Truth | Ref |
|---|---|---|
| `PostToolUse` sees tool failures | It fires on success only. Failures fire `PostToolUseFailure` with a top-level `error` string whose first line is usually `Exit code N`. | A1, A2 |
| Parse the transcript in the Stop hook | The transcript lags and its format is internal. Use `last_assistant_message`, `UserPromptSubmit.prompt`, and our own ledger. | A7, A12, D-001 |
| Policy ends with "else allow" | `allow` skips the user's permission prompt. Emit only `deny`, `ask`, or nothing. | A5, D-006 |
| Stop output uses `blockDecision` | Top-level `decision: "block"` plus `reason`. | A8 |
| `permissionDecision` can be `"block"` | Values are `allow`, `deny`, `ask`, `defer`. | A5 |
| Print a flag on stderr at Stop | Stderr on exit 0 is invisible. Use JSON `systemMessage`. | A15, A16 |
| Stop has a 1.5 s platform budget | That is `SessionEnd`. Stop's 2.5 s is our own target. | A17 |
| A PreToolUse hook is a security boundary | Timed-out hooks do not block. Seatbelt, not sandbox. | A6, D-007 |
| Call Jev on every tool call | Evidence gates; Jev runs at Stop only, when needed. | D-009 |
| Ask "every claim is supported by the ledger" | Jev is weak at universal and multi-hop questions. One noul per claim, aggregate in code. | C9, D-010 |
| Threshold on noul `confidence` | Noul answers have no confidence field. | C3 |
| Use `jev-latest` | Pin `jev-1.13.0` (direct) or `typesafe/jev-1.13-20260917` (OpenRouter). Store the returned id. | C7, D-011 |
| OpenRouter does not serve Jev | It does, hidden unless you pass `?output_modalities=all`. Endpoint `POST https://openrouter.ai/api/v1/systemone`. | D1 to D3 |
| Requesty, LiteLLM, Netlify are fallbacks | Unverified. Removed. | D5 |
| SQLAlchemy and Alembic for the local store | JSONL source of truth, stdlib sqlite3 derived index. | D-005 |
| "Jev is stochastic, report mean and std of 3 runs" | Bootstrap CIs over items, plus a separate identical-request stability check. | D-018 |
| ECE with 10 bins at n=300 is a headline | A perfectly calibrated model scores about 0.040 from noise. Always show the null band. | D-018 |
| "Raw events never leave the machine" | Verification sends a redacted summary to the provider. Say so plainly. | D-017 |
| "About 68 percent agreement", "four eval workflows", showcase email | No source exists. Never repeat. | C12 |
| Telemetry is anonymous | Pseudonymous at best. It is post-launch and opt-in. | PLAN 12 |
| RL and reward-model extensions are backlog | Blocked behind a legal gate. | C13 |
| The subagent tool is `Task` | It is `Agent`. No tool is named `Task`. | A21 |
| One `prompt_id` covers a task and its corrections | Every user prompt gets a new `prompt_id`. Use the verification span. | A10, D-020 |
| `PostToolUseFailure` can only add context | It also honors `decision: "block"` with a `reason`, delivered as a tool error. | A2 |
| 14-day retention from day one | 45 days during the study; never prune unlabeled corpus sessions. | D-021 |
| First Jev Stop hook | jev-belay shipped first. Credit it and use it as a baseline. No "first" or "only". | F1 |
| A `pre` row without a `post` row is a failed step | Hook-denied calls fire no Post event at all. Treat orphan pre rows as denied or unknown. | G15 |

## Architecture in brief

- `plugin/`: self-contained Claude Code plugin. `hooks/hooks.json` (exec form, synchronous), `hooks/run.sh` launcher, `hooks/verdict_hook.py` entry, `hooks/verdict_hot/` stdlib modules, `policies/default.json`. This directory is the source of truth for hot-path code.
- `src/agent_verdict/`: CLI and tools (`stats`, `show`, `doctor`, `index`, `replay`, `purge`, `export`, `migrate`, `policy lint`, `label`, `report`, `eval`, `import`). A verbatim copy of the hot-path modules lives at `src/agent_verdict/verdict_hot/`, written by `scripts/sync_hot.py`; edit only the `plugin/` copy. Python 3.11+. Heavy dependencies live only in the `report` and `eval` extras.
- `src/agent_verdict/adapters/transcript_v1.py`: the only code allowed to know the transcript format. Never imported by a hook.
- Data root: `verdict_home()` returns `$VERDICT_HOME` if set, else `~/.verdict`. No code expands `~/.verdict` directly.
- Data: `~/.verdict/events/<session_id>.jsonl` (append-only, `schema_v`, 0600), `~/.verdict/labels.jsonl`, `~/.verdict/hook.log`, derived `~/.verdict/index.db`. Not in `CLAUDE_PLUGIN_DATA`, which uninstall deletes.
- Events registered: SessionStart, UserPromptSubmit, PostToolUse (`^(Bash|Write|Edit|NotebookEdit|WebFetch|Agent|mcp__.*)$`), PostToolUseFailure (`*`), Stop, SessionEnd from M1; PreToolUse (`^(Bash|Write|Edit|NotebookEdit)$`) and SubagentStop from M2. No handler except Stop and SubagentStop ever touches the network.
- Evidence is gathered over a **verification span** of prompts, because each user prompt has its own `prompt_id` (D-020). `prompt_id` and `agent_id` are stored as explicit `null` when absent.
- Modes: `off`, `shadow` (default until the pre-registered gate is met), `enforce`. Loop guard: at most 1 block per `(session_id, prompt_id, agent_id)`.
- Providers: one `SystemOneClient`, presets `openrouter` and `typesafe`. SSL context falls back to the system CA bundle when the default store is empty (one Python on the owner's PATH fails TLS otherwise).

## Commands (none exist yet; the milestone that builds each is in brackets)

```
make setup | check | test | test-fast          # [M0] check = ruff, mypy --strict, import-ban and stdlib-shadow test,
                                               #      hot-tree diff, ledger-schema test, findings-ledger test
make bench-hook                                # [M1] cold-start and recorder latency budgets
make bench-provider                            # [M0 script, M2 target] needs a provider key
make capture-fixtures                          # [M0] real hook stdin via claude -p in a temp working directory
make plugin-validate                           # [M1] claude plugin validate ./plugin --strict
make e2e-cheap                                 # [M1] headless session with --include-hook-events on a task built to fail
make gen-redact                                # [M1] compile pinned gitleaks rules into a stdlib regex module
make doctor [M1] | replay [M2] | index [M3] | report [M3]
make eval | eval-guard | freeze-reporting-set  # [M4]
# run one hook by hand:
VERDICT_HOME=$(mktemp -d) plugin/hooks/run.sh post-fail < tests/fixtures/hooks/post_fail_bash.json; echo "exit=$?"
```

Tooling: `uv`, `ruff`, `mypy --strict`, `pytest` with `hypothesis`, conventional commits, semantic versioning. Frozen dataclasses and TypedDict with hand-written strict parsers, no mutation. Files under 400 lines, functions under 50. macOS and Linux supported; CI runs both.

## Evaluation rules

- The unit is one Stop event. Split by session: `real-tune` 40 percent, `real-report` 60 percent frozen by digest. Synthetic data never sets a threshold.
- `docs/EVAL_PREREG.md` is tagged `prereg-v1` before the reporting split is first read. The owner approves it first.
- Claims are tier-gated in code. Tier 1 (any n): AUROC and reliability diagram with bootstrap CI and null band, labeled pilot. Tier 2 (at least 600 labeled real events and 150 positives for the key): precision, recall, F1. Tier 3 (at least 1,000 and 200): any comparison or the words "better" or "outperforms". Below a tier, print "gate not met".
- Labeling is blinded by default. Raw text never leaves the machine that produced it. Only derived rows, numeric answers, and labels are committed under `data/goldset/`.
- `verdict eval` refuses model aliases and aborts if a provider returned more than one model id.
- Spending caps are in PLAN section 9.8, and every harness aborts at its cap.
- Before writing LLM-judge baseline code, load the `claude-api` skill to confirm current model ids and prices.

## Security and privacy checklist before any commit

No secrets in the repo. No free text under `data/goldset/`. `~/.verdict` modes are 0700 and 0600. The sentinel-key leak test, export free-text test, and report XSS test pass. GitHub Actions are pinned by commit SHA. PyPI uses Trusted Publishing. The plugin has an explicit `version`.

## Open decisions (owner)

O-1 name (`agent-verdict` by default, or rename to `exitproof` or `tracejudge` before the first public commit). O-2 Jev PreToolUse gate (deferred by default). O-3 hosted stack before launch (deferred by default). O-4 default provider (decided by the M0 benchmark). O-5 contributors (3 to 5 by default). Details in PLAN section 3.
