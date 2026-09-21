# Decisions Log

One entry per non-obvious choice. Newest at the bottom. Each entry: context, decision, reason, and what would reopen it.
This file outranks `docs/PLAN.md`. `docs/VERIFIED_FACTS.md` outranks both. Fact references like A1 or E3 point into that file.
Entries D-001 to D-021 were made during planning on 2026-09-20, before any code existed. **Status: confirmed by the owner on 2026-09-20 (D-022).**

---

### D-001 The tool ledger comes from Verdict's own hook events, never from the transcript
**Context.** The original plan parsed `transcript_path` at Stop time. The transcript lags, may miss the final message, and its format is internal and unstable (A7, A12). `PostToolUse` never sees failures (A1).
**Decision.** Record `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, and `Stop` into our own ledger keyed by `session_id`, `prompt_id`, `tool_use_id`. The user task comes from `UserPromptSubmit.prompt`. The final message comes from `Stop.last_assistant_message`. Transcript parsing lives in one versioned adapter used only by `verdict import` and the playground.
**Reason.** Correctness and stability. It is also the differentiator against jev-belay, which reconstructs what ran by regex over the transcript (F1).
**Reopen if.** Claude Code documents a stable transcript schema.

### D-002 Failure status is a deterministic fact, not a model judgment
**Decision.** A ledger row has `status=error` because `PostToolUseFailure` fired. `exit_code` is parsed only when the first line matches `^Exit code (\d+)$`, else NULL. The model never overturns an exit code. Jev judges only what needs language understanding: what the final message claims, whether it acknowledges failures, and soft failures inside successful output.
**Reason.** A2, and Jev's documented weakness to injected text (C9).

### D-003 The hook hot path is stdlib-only Python, vendored inside the plugin
**Context.** The planned stack (pydantic, SQLAlchemy, httpx, yaml) costs 154 ms to import, above the plan's own 150 ms startup budget. Stdlib-only costs about 25 to 38 ms (E1). Marketplace plugins cannot reach files outside their directory (B7).
**Decision.** Everything under `plugin/hooks/` imports only the standard library, supports Python 3.9+, and is the source of truth for hot-path code. The `agent-verdict` PyPI package carries the heavy extras (`report`, `eval`, `dashboard`) and imports the same hot-path modules. A CI test fails on any non-stdlib import under `plugin/hooks/` and on cold start above 60 ms.
**Reason.** Latency, one-step install, and a minimal supply chain on code that runs in every session.
**Reopen if.** Never for the hot path.

### D-004 All recorder hooks are synchronous and make no network calls
**Context.** One review proposed `async: true` recorders. Async hooks are killed at teardown under `claude -p`, cannot be bounded by `timeout`, and race the Stop check (A13).
**Decision.** `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `SessionStart`, `SessionEnd` handlers append one row and exit. Target under 40 ms. No async hooks in v1.
**Reason.** A missing ledger row is a silent false negative in the one place the product cannot have one.

### D-005 Append-only JSONL is the source of truth; SQLite is a derived index
**Context.** Two reviews conflicted: raw sqlite3 with WAL pragmas versus JSONL. Parallel hook processes contend for SQLite write locks. A second schema owner (SQLAlchemy for a dashboard) would drift.
**Decision.** The hot path appends to `~/.verdict/events/<session_id>.jsonl` with `O_APPEND`, mode 0600, one `write()` per row under `flock`. Every row carries `schema_v`. Labels are append-only `labels.jsonl`. `verdict index` builds a disposable stdlib `sqlite3` database for reports, labeling, and eval. No SQLAlchemy and no Alembic locally.
**Reason.** No lock contention, no migrations on the hot path, trivially inspectable, natural replay. Data lives in `~/.verdict`, not `CLAUDE_PLUGIN_DATA`, because uninstall deletes the latter (B6).
**Reopen if.** Stop-time ledger assembly exceeds 15 ms for 500 events.

### D-006 Verdict never emits `permissionDecision: "allow"`
**Context.** The original policy ended with "else allow". `allow` skips the user's permission prompt (A5), so a probabilistic classifier would silently widen permissions.
**Decision.** The pass case is exit 0 with empty stdout. Only `deny`, `ask`, or no decision are possible. A unit test proves no answer vector can produce `allow`.

### D-007 v1 PreToolUse gate is deterministic rules only; the Jev gate is a later experiment
**Context.** A Jev call on every mutating tool call has no latency headroom on the direct API (E2), duplicates a mature rule engine (F2), leaks the very secrets it asks about, and has no reachable real positives for evaluation.
**Decision.** v1 ships a small rule pack: `deny` for a destructive-command denylist and `ask` for credential paths on the never-send list. Rules fail closed on internal error (exit 2). The README recommends installing destructive_command_guard alongside. The Jev gate (`in_scope` and similar) is an M6 experiment that ships only if it beats the rule pack on the same gold set and the measured p90 fits the budget.
**Reason.** Honest positioning: a hook is a seatbelt, not a sandbox (A6).
**Owner may override.** This narrows the original thesis, so it is listed as an open decision in the plan.

### D-008 Hook matchers: mutate-or-egress tools only
**Decision.** `PreToolUse` matcher `^(Bash|Write|Edit|NotebookEdit)$`. `PostToolUse` matcher `^(Bash|Write|Edit|NotebookEdit|WebFetch|Agent|mcp__.*)$` (the subagent tool is `Agent`; no tool is named `Task`, A21). `PostToolUseFailure` matcher `*`. `SessionStart` has no matcher, so every source including `compact` is recorded. Read, Glob, Grep, and WebSearch successes never spawn a process. Tool names are confirmed against captured fixtures in M0 before the matchers are frozen.
**Reason.** A14. Read-only success rows add cost and little evidence. All failures stay visible.

### D-009 Evidence gates decide whether Jev is called at all
**Decision.** At Stop, call Jev only if the prompt's ledger has an unresolved error or soft-fail candidate, or the final message matches the success-claim pattern, or it claims a check passed while the ledger shows no passing check after the last change. Otherwise allow with no network call. Report the Jev reach rate per hook. Research mode (`always_verify`) overrides the gate for data collection.
**Reason.** jev-belay reaches Jev on 17.7 percent of stops and still reports AUROC 0.976 (F1). Gating also minimizes data egress.

### D-010 Questions are decomposed to fit Jev's documented weaknesses
**Decision.** No universal quantifiers, no conditional instructions, no negation pairs combined by arithmetic. Claims are extracted in code and judged one noul per claim in a single request (parallel questions are nearly free). Composite rules are evaluated and reported as single binary predictors. State is a JSON object with `trusted_facts` and `untrusted` sections, and every instruction says text under `untrusted` is data. Target state size is under 4,000 tokens; hard cap 22,000.
**Reason.** C9, C4.

### D-011 One SystemOne HTTP client, two provider presets, pinned model ids
**Decision.** `SystemOneClient(base_url, api_key, model, deadline)` over `http.client`. Presets: `typesafe` (`https://api.typesafe.ai`, model `jev-1.13.0`) and `openrouter` (`https://openrouter.ai/api`, model `typesafe/jev-1.13-20260917`). Aliases are refused by `verdict eval`. The response `model` is stored on every verdict row. The default provider is whichever has the lower measured p90 in M0. Requesty, LiteLLM, and Netlify are dropped (D5).
**Reason.** D3, C7, E2.

### D-012 TLS robustness is handled in process, not by pinning an interpreter
**Context.** The first `python3` on this machine's PATH cannot verify certificates (E3). A fail-open hook would silently never reach the provider.
**Decision.** Build the SSL context from the default store and, if it is empty, load the first existing of `/etc/ssl/cert.pem`, `/etc/ssl/certs/ca-certificates.crt`, `/etc/pki/tls/certs/ca-bundle.crt`. Verification is never disabled. `verdict doctor` and the `SessionStart` hook report an unreachable provider visibly.

### D-013 Policy is JSON with a JSON Schema; user-facing knobs live in plugin `userConfig`
**Decision.** `plugin/policies/default.json`, validated by `schemas/policy-v1.json`, every threshold named and tested. Overrides: `CLAUDE_PLUGIN_OPTION_*` then `VERDICT_*` env then `~/.verdict/policy.json` then packaged default. Project-level policy files are ignored, mirroring B5, so a cloned repo cannot relax the gate. The API key is read only from the environment, never from disk, and never appears in argv (B4).
**Reason.** YAML needs a third-party parser on the hot path, and plugin-only installs have no compiler step.

### D-014 Modes: `shadow` first, `enforce` after the evidence exists
**Decision.** Modes are `off`, `shadow` (record and judge, never block or nudge), and `enforce`. Default is `shadow` through M4. The default flips to `enforce` at v1.0 only if the pre-registered block precision gate is met. `VERDICT_DISABLE=1` is a kill switch checked before any other work.
**Reason.** Shadow mode builds the dataset without annoying anyone and supports the randomized comparison the causal claims need.

### D-015 Loop guard is per prompt, with stand-down rules
**Decision.** At most 1 block per `(session_id, prompt_id, agent_id)` by default, configurable to 2, never the same reason twice. Stand down when `stop_hook_active` and the budget is spent, when `background_tasks` is non-empty, and when `permission_mode` is `plan`. Keep verifying under `bypassPermissions`. `SubagentStop` records and judges but never blocks by default. The guard key allows `None` for `prompt_id` and `agent_id`. The guard key is per prompt even though the evidence span can cover several prompts (D-020).
**Reason.** A7, A8. One reviewer wanted to skip `bypassPermissions`; unattended runs are where an unsupported "done" costs the most, so that part was rejected.

### D-016 Block reasons and nudges never contain raw tool output
**Decision.** A block reason lists step number, tool, exit code, and a sanitized command of at most 80 characters, and points to `verdict show`. Self-imposed cap of 2,000 characters. The PostToolUse nudge is off by default and exists only as a measured experiment arm.
**Reason.** The reason stays in Claude's context (A8), so echoing attacker-controlled output would amplify prompt injection. It also taxes the user's context window.

### D-017 Privacy invariants
**Decision.** (1) A deterministic never-send list runs before any provider call; a match sends nothing. (2) One `redact()` function, generated from a pinned gitleaks rule set plus entropy rules, is called at exactly three sites: provider request, ledger write, export. A redactor exception sends nothing. (3) `~/.verdict` is 0700 and its files 0600; retention defaults to 45 days during the study (M1 through M4) and 14 days from v1.0, and the prune never removes unlabeled corpus sessions (D-021); `verdict purge` exists. (4) Only derived features, numeric answers, and labels are ever committed or shared; raw text never leaves the machine that produced it. (5) `local-only` mode makes no network calls. (6) The README states plainly what is sent to the provider.
**Reason.** The original plan promised "raw events never leave the machine" while sending tool inputs and outputs to a third party on every call.

### D-018 Evaluation claims are gated in code
**Decision.** `verdict eval` prints a metric only when its tier is met. Tier 1 (any n): AUROC and reliability diagram with a stratified item-bootstrap 95 percent CI and a calibrated-null band, labeled pilot. Tier 2 (at least 600 labeled real events and 150 positives for that key): precision, recall, F1 at the shipped threshold. Tier 3 (at least 1,000 and 200): any between-system comparison or the words "better" or "outperforms". Metrics, the primary endpoint, and the threshold rule are pre-registered in `docs/EVAL_PREREG.md` and tagged before the reporting set is read. Thresholds are tuned on a real tuning split, never on synthetic data and never on the reporting split. Splits are by session.
**Reason.** At n=300 with 60 positives the CI half-width is 8 to 10 points, and a perfectly calibrated model scores ECE 0.040 from noise alone.

### D-019 The launcher passes `-S` and never `-E`
**Context.** The first draft ran the interpreter with `-S -E`. Measured on Apple's `/usr/bin/python3` 3.9.6, `-E` raises the stdlib import cost from about 34 ms to about 101 ms, while `-S` is free (E1). `-S` alone already blocks site-packages.
**Decision.** `exec "$PY" -S verdict_hook.py`, after the launcher unsets `PYTHONPATH`, `PYTHONHOME`, and `PYTHONSTARTUP`. The import-ban test also rejects any file under `plugin/hooks/` that shadows a stdlib module name, because the script directory stays at `sys.path[0]`.
**Reopen if.** A supported interpreter shows a different cost profile in `make bench-hook`.

### D-020 Evidence is gathered over a verification span, not a single prompt
**Context.** Each user prompt has its own `prompt_id` (A10). A failure recorded before the user's correction would be invisible to a verifier scoped to one prompt, which is exactly the case the product exists to catch.
**Decision.** The span is the current prompt plus earlier prompts in the session, walking back to the first clean stop, 5 prompts, or a `clear`. A failure stays open until resolved by a later `ok` row or acknowledged in an earlier stop (`acks_failures >= t_ack_hi`). Acknowledged failures never trigger R1 again. The loop guard remains keyed per prompt (D-015).
**Reason.** Catches carried-over failures without re-blocking on something the user was already told.
**Reopen if.** Labeled data shows span-carried failures cause false blocks above the pre-registered precision gate.

### D-021 The study corpus is protected, and the collector ships only with a measured redactor
**Context.** Collection starts on day 3 but labeling starts on day 13, so a 14-day prune would delete the irreplaceable corpus. Contributors install on day 4, so an unmeasured redactor would write their data for eight days.
**Decision.** Retention is 45 days during the study and the prune skips unlabeled corpus sessions. The full redactor, its corpus, and gate G1.7 (recall at least 0.95, false-positive rate at most 0.02, sentinel-key test) move into M1, and the collector is not sent to anyone until G1.7 passes. Pattern tagging and claim extraction also move into M1 so the earliest rows carry those fields. M1 grows to about 16 hours and M2 shrinks to about 16.

### D-022 Owner confirmation and open decisions
**Decision.** The owner confirmed the plan on 2026-09-20 and accepted every default. O-1: the single identifier is `agent-verdict`, import `agent_verdict`, CLI `verdict`. O-2: the Jev PreToolUse gate is deferred to M6. O-3: hosted site, playground, telemetry, Docker, and the web dashboard are post-launch. O-4: OpenRouter is the expected default provider, to be confirmed by the M0 benchmark once the owner supplies a key. O-5: the owner will try to recruit contributors (aim for 7, need 5 for a pooled headline); the single-author pilot is the fallback.

### D-023 M0 results
**Context.** M0's gates (PLAN section 7) were run against real tooling and real captured hook fixtures, not documentation alone.
**Decision.** G0.1 passed for the name `agent-verdict` (`scripts/check_name.sh agent-verdict` exited 0, free on PyPI, npm, and as a GitHub user). G0.3 is met: 16 real, sanitized fixtures live under `tests/fixtures/hooks/`, one per event or tool variant, each with a provenance line (`tests/fixtures/hooks/PROVENANCE.md`). G0.4 (CI green) is pending the first CI run after the repository is pushed to a remote; no remote is configured yet. G0.2 (the provider benchmark) stays open until the owner supplies an OpenRouter key; `scripts/smoke_jev.py` and the `make bench-provider` target are implemented and ready to run as soon as a key is available. Adopt the orphan-pre-row rule (G15, `docs/VERIFIED_FACTS.md` section G): a hook-denied `PreToolUse` call has no matching `PostToolUse` or `PostToolUseFailure`, so a `pre` row with no `post` row means denied or unknown, never a failure. During fixture capture, the owner's global PreToolUse gate hook did not block any of the benign headless commands (`echo hello`, the failing `sh -c` case, the file edit, the subagent's `echo sub`), so `--setting-sources` was not needed to bypass it and was not added to `scripts/capture_tasks.sh`.
**Reason.** Record what M0 actually verified, separately from what remains blocked on external inputs (the OpenRouter key, the push to a remote), so the milestone is not tagged prematurely (see G0.2 in `CLAUDE.md`).
**Reopen if.** G0.2 or G0.4 fail once unblocked.
