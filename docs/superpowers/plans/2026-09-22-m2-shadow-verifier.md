# M2 Shadow Verifier v0.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At every Stop, build the evidence span from the ledger, decide with cheap gates whether a model call is needed, ask Jev one batch of typed questions, apply the versioned policy, and record the verdict and action, in `shadow` mode by default (never blocks) and `enforce` mode when configured. Plus a deterministic PreToolUse rules gate, replay, purge, export, and policy lint.

**Architecture:** Stop handling stays inside the existing stdlib hot path (`plugin/hooks/verdict_hot/`): new modules `span`, `state`, `questions`, `provider`, `verdict_policy`, `guard`, `rules`, `stop`. The entry point gains the `stop`, `subagent-stop`, and `pre` events and, for the first time, may print JSON to stdout (a block or a flag). CLI additions live in `src/agent_verdict/`.

**Tech Stack:** Python 3.9 stdlib on the hot path (`http.client`, `ssl`, `json`), Python 3.11+ for the CLI, pytest, hypothesis, recorded provider cassettes for offline tests.

**Spec:** `docs/PLAN.md` sections 4.2, 4.3, 5.0, 5.1, 5.3 to 5.6, 7 (M2); `docs/VERIFIED_FACTS.md` (A5 to A9, A15, A16, C1 to C9, D1 to D3, E6, G); `docs/DECISIONS.md` D-002, D-004, D-006, D-007, D-009 to D-016, D-019, D-020, D-025, D-028, D-029. DECISIONS outranks PLAN.

## Global Constraints

- Everything under `plugin/hooks/` is stdlib only, Python 3.9 compatible, `typing.NamedTuple` not dataclasses (D-028), relative imports, lazily imported per event, functions under 50 lines, files under 400 lines. Edit only `plugin/hooks/verdict_hot/`; `make sync-hot` mirrors it.
- **The only network call on the hot path is the Stop and SubagentStop provider request** (D-004). PreToolUse makes none in v1 (D-007). Recorders are unchanged.
- **Fail open on the model path, fail closed on the rules path.** Any exception, timeout, or provider error in Stop handling records action `gate_unavailable` and exits 0 with empty stdout. The PreToolUse rules gate exits 2 with a one-line stderr on an internal error.
- **Never emit `permissionDecision: "allow"`** (D-006). PreToolUse emits `deny`, `ask`, or nothing.
- Stop output contract (A8, A16): block is `{"decision":"block","reason":"..."}` on exit 0; flag is `{"systemMessage":"Verdict: ..."}` on exit 0; pass is exit 0 with empty stdout. Nothing else is ever printed. In `shadow` mode the output is always pass; the row records `would_have`.
- The block reason never contains raw tool output: step number, tool, exit code, and a sanitized command of at most 80 chars per step, capped at 2,000 chars total (D-016).
- State sent to the provider passes through the same never-send check and `redact()` as ledger rows, is a JSON object with `trusted_facts` and `untrusted` sections, and is capped at 22,000 tokens (approximated as chars / 4) with a 4,000 target (D-010, C4). Never-send rows contribute only their structural fields.
- Model ids are pinned per provider: `typesafe/jev-1.13-20260917` on OpenRouter, `jev-1.13.0` on TypeSafe; aliases are refused (D-011). The returned `model` is stored on every verdict row.
- Provider timing: total Stop budget 2.5 s; client connect+read deadline 1.8 s; one retry only if at least 1.2 s remains; three consecutive 429s open a 10-minute circuit breaker stored in `~/.verdict/breaker.json`.
- API key comes from `CLAUDE_PLUGIN_OPTION_API_KEY`, else `OPENROUTER_API_KEY` or `TYPESAFE_API_KEY`, never from a file, never in argv, never logged (B4, D-013). `redact()` already removes it by value.
- Loop guard: at most `max_blocks_per_prompt` (default 1, ceiling 2) blocks per `(session_id, prompt_id, agent_id)`, never the same `reason_hash` twice, derived from `action` rows (D-015). Stand down (record, do not block) when `stop_hook_active` and the budget is spent, when `background_tasks` is non-empty, when `permission_mode == "plan"`, on SubagentStop unless `subagent.block` is true.
- Every new row type (`verdict`, `action`, `pre`) is added to `schemas/ledger-v1.json` and the validator, with `schema_v` unchanged (fields are additive).
- Tests never call a real provider: `RecordedFixture` cassettes under `tests/fixtures/cassettes/` recorded once with the owner's key by `scripts/record_cassettes.py` (run by the controller, not by workers), plus one live smoke test behind `--live`. Every subprocess gets explicit stdin and a timeout.
- Implementers commit on branch `m2` with conventional commits, no attribution trailers, never push.

## File Structure

| File | Responsibility |
|---|---|
| `verdict_hot/span.py` | verification span from ledger rows (D-020), open and resolved failures, checks-after-last-change |
| `verdict_hot/state.py` | JSON state object with budgeting and error-anchored compression |
| `verdict_hot/questions.py` | the question set for a state (golden-file tested) |
| `verdict_hot/provider.py` | `SystemOneClient`, presets, deadlines, retry, breaker, `RecordedFixture` hook point |
| `verdict_hot/verdict_policy.py` | rules R1 to R4, thresholds from policy, `decide(answers, facts) -> Decision` |
| `verdict_hot/guard.py` | loop guard over `action` rows |
| `verdict_hot/stop.py` | orchestrates Stop and SubagentStop: stand-down, gate G-STOP, state, provider, policy, rows, output |
| `verdict_hot/rules.py` | PreToolUse deterministic gate (denylist, never-send `ask`) |
| `plugin/hooks/verdict_hook.py`, `hooks.json`, `plugin.json` | new events, stdout for decisions, `api_key` and `provider` options |
| `plugin/policies/default.json`, `schemas/policy-v1.json` | thresholds, rules, provider presets, deadlines, denylist |
| `src/agent_verdict/{replay,purge,export,policy_lint,show}.py` | CLI additions |
| `scripts/record_cassettes.py` | records provider responses for the fixtures (controller runs it) |
| `tests/fixtures/cassettes/*.json` | recorded provider responses |

---

### Task 1: Verification span and evidence facts

**Files:** `verdict_hot/span.py`, `tests/unit/test_span.py`.

**Interfaces (Produces):**
- `Step(seq: int, tool: str, command: str, status: str, exit_code: int | None, is_check: bool, soft_fail_candidate: bool, resolved_later: bool, acknowledged: bool, tool_use_id: str, prompt_id: str | None, out_excerpt: str, never_send: bool)` NamedTuple.
- `Span(prompts: tuple[str, ...], steps: tuple[Step, ...], unresolved_failures: tuple[int, ...], soft_fail_seqs: tuple[int, ...], checks_passed_after_last_change: bool, span_prompt_ids: tuple[str | None, ...], reason: str)`.
- `build_span(rows: Sequence[Mapping[str, object]], current_prompt_id: str | None, policy: Policy) -> Span`: rows are one session's ledger rows in file order. Walk back from the current prompt through earlier prompts until the first of: a clean stop (an `action` row with `action == "pass"` and no open failures at that time), `policy.span.max_prompts` (default 5), or a `session_start` row with `source == "clear"`. Steps get `seq` numbers in span order starting at 1. A failure is resolved when a later row with the same tool and the same normalized command (or target path) has `status == "ok"`; it is acknowledged when an earlier `verdict` row in the span for a stop that listed it scored `acks_failures >= policy.thresholds.t_ack_hi` (default 0.7). Unresolved and unacknowledged failures are `unresolved_failures`. `checks_passed_after_last_change` is true when an `is_check` row with `status == "ok"` follows the last `post` row whose tool is Write, Edit, or NotebookEdit (or the last Bash row that is not a check).
- Null `prompt_id` rows form their own bucket (D-015 note).

**Required tests:** span stops at a clean stop; at the 5-prompt limit; at `clear`; a failure before a correction prompt is still open; the same command later succeeding resolves it; an acknowledged failure does not reappear; checks-after-last-change true and false cases; null prompt_id bucket; a 500-row session builds in under 15 ms (mark `slow`); Hypothesis: arbitrary row lists never raise.

- [ ] Tests first, implement, `make sync-hot && make check && make test`, commit `feat: build the verification span from ledger rows`.

---

### Task 2: State builder and question set

**Files:** `verdict_hot/state.py`, `verdict_hot/questions.py`, `tests/unit/test_state.py`, `tests/unit/test_questions.py`, `tests/golden/questions/*.json`.

**Interfaces (Produces):**
- `build_state(span: Span, final_message: str, claims: tuple[str, ...], policy: Policy) -> tuple[dict[str, object], bool]`: returns the state object exactly as PLAN 5.3 shows (`trusted_facts` with `user_task`, `steps`, `unresolved_failures`, `checks_passed_after_last_change`; `untrusted` with `final_message`, `claims` keyed `c1..cN`, `step_output_excerpts` for unresolved failures and soft-fail candidates only) and an overflow flag. Compression order when over `policy.state.target_tokens` (4,000; hard cap `max_tokens` 22,000, token = chars / 4): drop resolved `ok` steps oldest first, then shorten `step_output_excerpts` to head 200 and tail 600, then truncate `user_task`. Never drop error rows, soft-fail candidates, or check rows. Text fields are already redacted in the ledger; never-send steps contribute no excerpt.
- `build_questions(state: Mapping[str, object]) -> dict[str, dict[str, object]]`: exactly the PLAN 5.3 table: `claims_done`, `claims_check_passed` always; `acks_failures` when unresolved failures exist; `claim_c<i>` per claim; `softfail_<seq>` per candidate; `completion` score with the four levels. Every `instructions` ends with the fixed untrusted-data sentence. Nouls carry `criteria` with `true` and `false` descriptions.
- Golden files: for four fixture spans (no failures; one unresolved failure with two claims; soft-fail candidate; over-budget), the exact questions JSON is committed under `tests/golden/questions/` and a test asserts byte equality, so wording drift shows up in review.

**Required tests:** state shape; compression order and invariants (Hypothesis: for arbitrary spans the result is under the hard cap and every error, soft-fail, and check step survives); no raw never-send content; the untrusted sentence on every question; golden equality; question keys are valid identifiers.

- [ ] Tests first, implement, sync, check, commit `feat: add the provider state builder and the Stop question set`.

---

### Task 3: Provider client, cassettes, and circuit breaker

**Files:** `verdict_hot/provider.py`, `scripts/record_cassettes.py`, `tests/unit/test_provider.py`, `tests/fixtures/cassettes/README.md`.

**Interfaces (Produces):**
- `Preset(name, host, path, key_env, model)`; `PRESETS = {"openrouter": ("openrouter.ai", "/api/v1/systemone", "OPENROUTER_API_KEY", "typesafe/jev-1.13-20260917"), "typesafe": ("api.typesafe.ai", "/v1/systemone", "TYPESAFE_API_KEY", "jev-1.13.0")}`.
- `ProviderResult(ok: bool, answers: Mapping[str, object], model_returned: str | None, input_tokens: int | None, conn_ms: float, infer_ms: float, status: int, error: str | None)`.
- `evaluate(state, questions, preset: Preset, api_key: str, deadline_s: float, transport=None) -> ProviderResult`: `http.client.HTTPSConnection` with `sslctx.build_context()`, `TCP_NODELAY`, a monotonic deadline, JSON body `{"model", "state", "questions"}`, `Authorization: Bearer`; validates answers (each key present, `noul` in [0,1], `score` numeric); on non-200, malformed body, timeout, or exception returns `ok=False` with a fixed-string `error` (never response content). `transport` is an injectable callable `(host, path, body_bytes, headers, deadline) -> (status, body_bytes, conn_ms, infer_ms)` used by tests and cassettes.
- `Breaker`: file `~/.verdict/breaker.json` (0600) with `consecutive_429`, `open_until`; `is_open()`, `record(status)`; three 429s open it for 600 s; any 200 resets.
- `RecordedTransport(cassette_dir)`: matches a request by SHA-256 of the canonical JSON body and returns the recorded response; missing cassette raises `MissingCassette` (tests fail loudly, never call the network).
- `scripts/record_cassettes.py`: for every golden state in `tests/golden/questions/`, sends the real request with the owner's key and writes `tests/fixtures/cassettes/<hash>.json` with request body, response body (answers only; strip anything else), model, and usage. The CONTROLLER runs this once with `OPENROUTER_API_KEY` loaded; workers never do. If cassettes are missing when a worker runs the suite, the affected tests must skip with a clear message, not fail.

**Required tests:** request body shape and headers (key present in header, absent from every log line); answer validation rejects out-of-range and missing keys; timeout and non-200 paths return `ok=False` with fixed error strings; the deadline is respected (fake transport that sleeps); breaker opens after three 429s and closes after expiry; cassette matching round-trip; `redact()` was applied to the state before sending (transport-patching test).

- [ ] Tests first, implement, sync, check, commit `feat: add the System One provider client with cassettes and a circuit breaker`.

---

### Task 4: Policy rules, loop guard, Stop orchestration, hook wiring

**Files:** `verdict_hot/verdict_policy.py`, `verdict_hot/guard.py`, `verdict_hot/stop.py`, `plugin/hooks/verdict_hook.py`, `plugin/hooks/hooks.json`, `plugin/.claude-plugin/plugin.json`, `plugin/policies/default.json`, `schemas/policy-v1.json`, `schemas/ledger-v1.json`, tests `tests/unit/test_verdict_policy.py`, `test_guard.py`, `test_stop.py`, `tests/integration/test_stop_hook.py`.

**Interfaces (Produces):**
- Policy additions in `default.json`: `thresholds` {`t_done` 0.7, `t_ack` 0.3, `t_ack_hi` 0.7, `t_check` 0.7, `t_soft` 0.8, `t_claim` 0.25}, `stop` {`max_blocks_per_prompt` 1, `ceiling` 2, `always_verify` false, `subagent_block` false}, `provider` {`default` "openrouter", `deadline_s` 1.8, `budget_s` 2.5, `retry_min_remaining_s` 1.2, `breaker_open_s` 600}, `span` {`max_prompts` 5}, `state` {`target_tokens` 4000, `max_tokens` 22000}, `denylist` (PLAN 5.1 list as regexes).
- `decide(answers, span, policy) -> Decision(action: "pass"|"flag"|"block", rule_id: str | None, threshold_used: float | None, offending_seqs: tuple[int, ...], reason: str)` implementing R1 to R4 exactly; `reason` built from steps only (seq, tool, exit code, sanitized command 80 chars), capped at 2,000 chars.
- `guard.check(action_rows, session_id, prompt_id, agent_id, reason_hash, policy) -> GuardResult(allowed: bool, blocks_issued: int, reason: str)`.
- `stop.handle(payload, policy, now, api_key, transport=None) -> StopOutcome(stdout_json: str | None, rows: tuple[dict, ...], outcome: str)`: order is stand-down checks (write an `action` row with the skip reason and return pass), span, G-STOP gate (`gate_reason` recorded; skip the provider when nothing needs judging unless `always_verify`), claims, state, breaker check, provider call, `verdict` rows (one per question: `question_key`, `question_type`, `answer` numeric fields only, `provider`, `model_returned`, `input_tokens`, `conn_ms`, `infer_ms`, `policy_version`), `decide`, guard, `action` row (`action`, `would_have`, `rule_id`, `threshold_used`, `mode`, `reason_hash`, `gate_reason`, `hook_ms`), and the stdout JSON per the output contract. Shadow mode: `action` recorded as the enforce decision under `would_have`, actual action `pass`, no stdout. Provider failure: `gate_unavailable`, no stdout.
- `verdict_hook.py`: routes `stop` and `subagent-stop` to `stop.handle`, prints `stdout_json` when present, still exits 0 always; the 2.5 s budget is enforced with the monotonic clock passed into the provider deadline. `hooks.json` adds `SubagentStop` (matcher `*`) and sets `timeout` 15 on both Stop entries. `plugin.json` adds `userConfig` `api_key` (string, `sensitive: true`), `provider` (options `openrouter`, `typesafe`, `local-only`; default `openrouter`). `local-only` means never call a provider (record only).

**Required tests:** every rule with threshold boundary cases; composite rules as single predictors; reason format and cap; guard limits and duplicate-reason refusal; stand-down for each condition; G-STOP skips when no evidence; shadow never prints; enforce prints exactly the block JSON for the staged failure and the flag JSON for R4; provider failure gives `gate_unavailable` and empty stdout; budget: a transport that sleeps 3 s yields `gate_unavailable` within 2.6 s; integration through `run.sh` with the stop fixture plus a synthetic ledger and a cassette; a sentinel key never appears in rows or `hook.log`.

- [ ] Tests first, implement, sync, check, commit `feat: add the Stop verifier with policy rules, loop guard, and hook wiring`.

---

### Task 5: PreToolUse rules gate

**Files:** `verdict_hot/rules.py`, `plugin/hooks/hooks.json` (PreToolUse entry, matcher `^(Bash|Write|Edit|NotebookEdit)$`, `timeout` 3), `tests/unit/test_rules.py`, `tests/integration/test_pre_hook.py`, `tests/unit/test_policy_never_allows.py`.

**Interfaces:** `rules.decide(tool_name, tool_input, policy) -> RuleDecision(decision: "deny"|"ask"|None, rule_id, reason)`; the denylist comes from PLAN section 5.1 (destructive recursive deletes of the filesystem root, home, or repo root; force-push to protected branches; untracked-file purges; raw device writes and filesystem formatting; world-writable recursive permission changes on the root; downloads piped into a shell; database drop and truncate statements through a CLI client), expressed as regexes in the policy file; never-send paths and secret-reading commands return `ask` with a fixed reason. A `pre` row is recorded (`tool_use_id`, `tool_name`, `rule_id`, `decision`, `never_send`, no content). Output: `hookSpecificOutput` with `hookEventName: "PreToolUse"`, `permissionDecision`, `permissionDecisionReason` on exit 0; no decision means exit 0 with empty stdout. Internal exception: exit 2, one-line stderr `Verdict rules gate failed (<class>)`. `test_policy_never_allows` proves by construction that no input can yield `allow`.

**Required tests:** each denylist entry and near-misses (a recursive delete of a build directory inside the repo is not denied; a force-push with lease to a feature branch is not denied); `ask` for reading `.env`; exec-form output JSON exact; exit 2 path; through `run.sh` with the pre fixture; latency p50 under 60 ms through the launcher (bench with explicit stdin and timeouts).

- [ ] Tests first, implement, sync, check, commit `feat: add the deterministic PreToolUse rules gate`.

---

### Task 6: CLI: show, replay, purge, export, policy lint; stats and doctor additions

**Files:** `src/agent_verdict/{show,replay,purge,export,policy_lint}.py`, `cli.py`, `stats.py`, `doctor.py`, tests per module.

- `verdict show <session_id>`: timeline of prompts, steps, verdicts (probabilities), and actions for one session; `--prompt <id>` narrows.
- `verdict replay --policy <file> [--since <date>] [--json]`: re-runs `decide` over stored `verdict` rows and prints per action row old action, new action, and the threshold that moved it. Contract test: replaying the shipped policy reproduces recorded actions byte for byte; changing one threshold changes exactly the rows whose probability straddles it.
- `verdict purge [--session ID] [--older-than 7d] [--all]` with a confirmation prompt (skippable with `--yes`), and a `SessionEnd`-time bounded prune honoring `store.retention_days` (45 during the study, D-021) that never deletes a session with unlabeled stops inside the window.
- `verdict export --goldset --out <file>`: derived rows only (`event_id_hash`, `contributor_id`, `hook_kind`, `tool_name`, `question_key`, numeric answers, `exit_code`, counts, `model_returned`, `sha256_of_raw`); a test proves no exported value is free text or matches any redaction rule.
- `verdict policy lint <file>`: validates against `schemas/policy-v1.json` and the threshold ranges.
- `stats`: add Jev reach rate, actions by kind, `would_have` counts, `gate_unavailable` count, breaker state. `doctor`: add key presence for `CLAUDE_PLUGIN_OPTION_API_KEY`, breaker state, and a `--live-smoke` flag that sends one tiny real request (never in tests).

- [ ] Tests first, implement, check, commit `feat: add show, replay, purge, export, and policy lint`.

---

### Task 7: Staged failure scenario, docs, and switch to shadow with keys

**Files:** `tests/e2e/scenario_repo/` (a tiny project with a test that cannot pass), `scripts/e2e_cheap.py` (add `--scenario stop-block` running `enforce` and asserting a Stop `hook_response` with `decision: "block"` naming the failing step, exactly one block for the prompt, and a second scenario in `shadow` asserting no block and a `would_have: "block"` action row), `docs/PRIVACY.md` and `docs/CONSENT.md` (the switch: from v0.2 a redacted turn summary goes to the configured provider at Stop; exactly what is sent; how to stay local with `provider: local-only`), `README.md`, `docs/DECISIONS.md` (M2 results).

- [ ] Gates G2.1 to G2.6 per PLAN section 7 (coverage at least 90 percent on `verdict_hot`; property tests; replay contract; integration failure injection; the staged scenario; `bench_hook.py`: recorders p50 under 75 ms, Stop p50 under 900 ms and p95 under 2,500 ms on the default provider); commit `docs: describe verification egress and the local-only option`.
