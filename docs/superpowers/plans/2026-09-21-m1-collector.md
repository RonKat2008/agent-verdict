# M1 Collector v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A key-free, network-free Claude Code plugin that records every prompt, tool result, tool failure (with exit code), and stop into a redacted local ledger, plus `verdict stats` and `verdict doctor`.

**Architecture:** One POSIX launcher starts a stdlib-only Python entry point per hook event. The entry point parses stdin strictly, tags and redacts the payload, and appends one JSON line to a per-session file under the data root. Hot-path modules live in `plugin/hooks/verdict_hot/` and are copied verbatim into `src/agent_verdict/verdict_hot/` for the CLI.

**Tech Stack:** Python 3.9-compatible standard library for everything under `plugin/hooks/`; Python 3.11+ for `src/` and `scripts/`; pytest, hypothesis, ruff, mypy --strict; POSIX sh.

**Spec:** `docs/PLAN.md` sections 4.1 to 4.3, 5.0, 5.2, 6, and 7 (M1). `docs/VERIFIED_FACTS.md` and `docs/DECISIONS.md` outrank it. Real payload shapes: `tests/fixtures/hooks/*.json` and VERIFIED_FACTS section G.

## Global Constraints

- **Stdlib only under `plugin/hooks/`**, and the code must run on Python 3.9: start every module with `from __future__ import annotations`; no `match`, no `tomllib`, no `zip(strict=)`, no `dataclass(slots=True)`, no `X | Y` evaluated at runtime (annotations only).
- Modules inside `verdict_hot` import each other with **relative imports** (`from . import ledger`), because the same files run as `verdict_hot` (plugin) and `agent_verdict.verdict_hot` (CLI).
- `plugin/hooks/verdict_hot/` is the only place hot-path code is edited. `scripts/sync_hot.py` copies it to `src/agent_verdict/verdict_hot/`; `make check` fails when `diff -r` finds a difference.
- Recorders are synchronous and **never touch the network** (D-004). Nothing in M1 opens a socket except `verdict doctor`'s TLS probe.
- The model path fails open: any exception in a recorder is logged to `hook.log` and the process exits 0 with empty stdout. M1 emits no decisions at all (A15, A16).
- Data root: `verdict_home()` returns `Path(os.environ["VERDICT_HOME"])` if set, else `Path.home() / ".verdict"`. No other code expands `~/.verdict`. Directories are created 0700, files 0600, set explicitly and re-asserted on open.
- One `os.write` per ledger row on an `O_APPEND` descriptor under `fcntl.flock`. Never hold the lock across anything slow.
- Every row has `schema_v: 1`. `prompt_id` and `agent_id` are always present and are `null` when the payload lacks them (A10, G13). `scratchpad_dir` and `effort` are optional inputs (G14).
- A `pre` row with no `post` is denied or unknown, never a failure (G15). M1 records no `pre` rows.
- One `redact()` function; it runs before every ledger write. A redactor exception means the text field is replaced by `"[redaction failed]"`, never written raw.
- The never-send check runs before anything else touches payload text.
- `VERDICT_DISABLE` set to any non-empty value exits 0 before any other work. On Windows (`os.name == "nt"`) the entry point logs `disabled` and exits 0.
- Never print, log, or write an API key. `logsafe.scrub()` is applied to every string written to `hook.log`.
- M1 `hooks.json` registers only SessionStart, UserPromptSubmit, PostToolUse, PostToolUseFailure, Stop, SessionEnd (D-008, PLAN 7). PostToolUse matcher: `^(Bash|Write|Edit|NotebookEdit|WebFetch|Agent|mcp__.*)$`. Handlers are exec form through `${CLAUDE_PLUGIN_ROOT}/hooks/run.sh`. The launcher passes `-S` and never `-E` (D-019).
- Frozen dataclasses, no in-place mutation of inputs, functions under 50 lines, files under 400 lines.
- Implementers commit on branch `m1` with conventional commits, no attribution trailers, never push.

## File Structure

| File | Responsibility |
|---|---|
| `plugin/hooks/verdict_hot/__init__.py` | `SCHEMA_V = 1`, `PLUGIN_VERSION` |
| `plugin/hooks/verdict_hot/paths.py` | `verdict_home()`, directory and file creation with modes |
| `plugin/hooks/verdict_hot/ledger.py` | append one row, read a session, spool on newer schema |
| `plugin/hooks/verdict_hot/logsafe.py` | `scrub()`, `log_invocation()`, rotation, excepthook |
| `plugin/hooks/verdict_hot/textnorm.py` | NFKC, strip zero-width, bidi and ANSI; error-anchored truncation |
| `plugin/hooks/verdict_hot/_redact_rules.py` | GENERATED rule table (strings, not compiled) |
| `plugin/hooks/verdict_hot/redact.py` | `redact(text) -> (text, hits)` with keyword prefilter and lazy compile |
| `plugin/hooks/verdict_hot/policy.py` | load `default.json`, typed accessors |
| `plugin/hooks/verdict_hot/gates.py` | never-send check, G-SOFT and G-CHECK tagging |
| `plugin/hooks/verdict_hot/claims.py` | success-claim sentence extraction |
| `plugin/hooks/verdict_hot/parsers.py` | strict stdin parsers for the six M1 events |
| `plugin/hooks/verdict_hot/recorders.py` | payload to ledger row, per event |
| `plugin/hooks/verdict_hot/sslctx.py` | D-012 SSL context with CA fallback (used by doctor now, provider in M2) |
| `plugin/hooks/verdict_hook.py`, `plugin/hooks/run.sh`, `plugin/hooks/hooks.json` | entry point, launcher, registration |
| `plugin/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `plugin/policies/default.json` | manifests and policy |
| `schemas/ledger-v1.json`, `schemas/policy-v1.json` | JSON Schemas |
| `vendor/gitleaks.toml`, `scripts/gen_redact.py`, `tests/fixtures/secrets_corpus.jsonl` | redactor source, generator, measurement corpus |
| `scripts/sync_hot.py`, `scripts/bench_hook.py`, `scripts/e2e_cheap.py` | tree sync, latency gate, headless end-to-end gate |
| `src/agent_verdict/cli.py`, `src/agent_verdict/stats.py`, `src/agent_verdict/doctor.py` | CLI subcommands |
| `docs/CONSENT.md`, `docs/PRIVACY.md`, `README.md` | contributor consent, egress statement, install |

Tooling changes (Task 1): pytest `pythonpath = ["scripts", "plugin/hooks"]`; a second mypy invocation `uv run mypy --python-version 3.9 plugin/hooks`; `make check` adds the sync diff and the import-ban test; ruff `per-file-ignores` may relax `UP` rules under `plugin/hooks/` so 3.9-compatible syntax is not "upgraded".

---

### Task 1: Data root, ledger, safe logging, tooling for the hot tree

**Files:** `verdict_hot/__init__.py`, `paths.py`, `ledger.py`, `logsafe.py`, `schemas/ledger-v1.json`, `scripts/sync_hot.py`, `tests/unit/test_paths.py`, `test_ledger.py`, `test_logsafe.py`, `tests/test_import_ban.py`, `tests/test_hot_tree_in_sync.py`; modify `pyproject.toml`, `Makefile`.

**Interfaces (Produces):**
- `paths.verdict_home() -> Path`; `paths.ensure_private_dir(path: Path) -> Path` (creates parents, chmod 0o700); `paths.events_dir() -> Path`; `paths.session_file(session_id: str) -> Path` (rejects ids that are empty or contain `/`, `\`, `..`, or NUL by raising `ValueError`); `paths.pending_dir() -> Path`; `paths.hook_log() -> Path`.
- `ledger.append_row(row: Mapping[str, object]) -> Path`: requires `session_id`, `event`, `schema_v`; serializes with `json.dumps(..., separators=(",", ":"), ensure_ascii=False)` plus `"\n"`; opens with `os.open(path, O_WRONLY|O_APPEND|O_CREAT, 0o600)`, `fchmod 0o600`, `flock(LOCK_EX)`, exactly one `os.write`, unlock, close. Returns the file written.
- `ledger.read_session(session_id: str) -> list[dict[str, object]]`: skips blank and unparseable lines, never raises on a corrupt line.
- `ledger.iter_sessions() -> Iterator[Path]`.
- Spool rule: if the session file's first row has `schema_v` greater than `SCHEMA_V`, `append_row` writes to `pending_dir() / f"{session_id}.jsonl"` instead and returns that path.
- `logsafe.scrub(text: str) -> str`: removes every value of env vars named `CLAUDE_PLUGIN_OPTION_*`, `OPENROUTER_API_KEY`, `TYPESAFE_API_KEY`, `ANTHROPIC_API_KEY` (when non-empty and at least 8 chars), `Authorization: ...` header values, and tokens matching `sk-[A-Za-z0-9_-]{16,}`; replacement `<redacted>`.
- `logsafe.log_invocation(event: str, session_id: str | None, outcome: str, total_ms: float, err_class: str | None = None, extra: Mapping[str, object] | None = None) -> None`: one JSON line with `ts`, `schema_v`, `event`, `session_id`, `outcome`, `err_class`, `total_ms`; `outcome` in `ok|skipped|exception|disabled`; rotates `hook.log` to `hook.log.1` above 10 MB; never raises.
- `logsafe.install_excepthook() -> None`: replaces `sys.excepthook` with one that logs only the exception class name.
- `scripts/sync_hot.py`: `sync(src: Path, dst: Path) -> list[str]` copies `*.py` verbatim, deletes stale files in `dst`, returns changed names; `main()` exits 0.

**Required tests (write first, watch fail):**
- `verdict_home` honors `VERDICT_HOME`, defaults to `~/.verdict`; created dirs are 0o700 and files 0o600 even under `umask 0o022`.
- `session_file("../x")`, `""`, `"a/b"` raise `ValueError`.
- `append_row` then `read_session` round-trips; a row is exactly one line; unicode survives.
- Concurrency (gate G1.5): 16 processes (`multiprocessing`, spawn context) each append rows to the same session until 500 total; afterwards 500 parseable rows, every `(pid, n)` pair present exactly once, no interleaved lines. Mark `slow`.
- Spool: a file whose first row has `schema_v: 2` makes the next append land in `pending/`.
- `read_session` skips a corrupt line in the middle.
- `scrub` removes a sentinel key placed in `CLAUDE_PLUGIN_OPTION_API_KEY`, an `Authorization: Bearer abc...` string, and an `sk-or-v1-...` token; leaves ordinary text alone.
- `log_invocation` writes valid JSON, never raises when the data root is unwritable (point `VERDICT_HOME` at a file), rotates above the size limit (monkeypatch the limit to 200 bytes).
- Import ban (`tests/test_import_ban.py`): AST-walk every `.py` under `plugin/hooks/`; every imported top-level module must be in `sys.stdlib_module_names` or be a relative import; no file stem under `plugin/hooks/` may equal a stdlib module name.
- Sync test: after `sync`, `filecmp.dircmp` shows no differences; a stale file in `dst` is removed.
- Ledger schema test: every row written in these tests validates against `schemas/ledger-v1.json` using a small hand-written validator in the test (required keys and types per event; no third-party jsonschema dependency).

- [ ] Step 1: update `pyproject.toml` and `Makefile` as described under "Tooling changes"; add `make sync-hot` and call it from `make setup`; `make check` runs ruff, both mypy invocations, `diff -r plugin/hooks/verdict_hot src/agent_verdict/verdict_hot`, the ledger and import-ban tests.
- [ ] Step 2: write the tests above. Run `uv run pytest tests/unit tests/test_import_ban.py tests/test_hot_tree_in_sync.py`; expect failures for missing modules.
- [ ] Step 3: implement the modules to the interfaces above.
- [ ] Step 4: run `make sync-hot && make check && make test`; expect pass. Also run the unit tests under Apple's Python to prove 3.9 compatibility: `/usr/bin/python3 -S -c "import sys; sys.path.insert(0,'plugin/hooks'); import verdict_hot.ledger, verdict_hot.logsafe, verdict_hot.paths; print('ok 3.9')"`.
- [ ] Step 5: commit `feat: add data root, append-only ledger, and safe hook logging`.

---

### Task 2: Text normalization, truncation, and the measured redactor (gate G1.7)

**Files:** `verdict_hot/textnorm.py`, `verdict_hot/redact.py`, `verdict_hot/_redact_rules.py` (generated), `vendor/gitleaks.toml`, `scripts/gen_redact.py`, `tests/fixtures/secrets_corpus.jsonl`, `tests/unit/test_textnorm.py`, `tests/unit/test_redact.py`, `tests/test_redaction_gate.py`; modify `Makefile` (`gen-redact`).

**Interfaces (Produces):**
- `textnorm.normalize(text: str) -> tuple[str, int]`: NFKC; remove `​-‏`, `‪-‮`, `⁦-⁩`, `﻿`; remove ANSI CSI and OSC sequences; returns the text and the count of removed characters.
- `textnorm.truncate_anchored(text: str, head: int, tail: int) -> str`: unchanged when `len(text) <= head + tail`; else `head` chars, the marker `\n... [N characters truncated] ...\n`, then `tail` chars. Never drops the tail.
- `redact.redact(text: str) -> tuple[str, int]`: replaces each secret span with `[REDACTED:<rule_id>]`, returns hit count. Deterministic, idempotent (`redact(redact(x)[0])` adds no hits).
- `_redact_rules.RULES: tuple[tuple[str, str, tuple[str, ...], float | None, int], ...]` = `(rule_id, pattern, lowercase_keywords, entropy_floor_or_None, secret_group)`; `GITLEAKS_COMMIT: str`.

**Design rules:**
- Vendor the default config from the gitleaks repository (MIT): fetch `config/gitleaks.toml` at a pinned commit with `gh api repos/gitleaks/gitleaks/contents/config/gitleaks.toml?ref=<sha> -H "Accept: application/vnd.github.raw"`; record the sha in `vendor/GITLEAKS_VERSION` and in `_redact_rules.GITLEAKS_COMMIT`. Include the license text as `vendor/GITLEAKS_LICENSE`.
- `scripts/gen_redact.py` (Python 3.11+, uses `tomllib`) translates each rule: convert Go RE2 syntax that Python rejects (leading or mid-pattern `(?i)` becomes a compile flag; `\z` becomes `\Z`; named groups `(?P<x>` stay; POSIX classes such as `[[:alnum:]]` become explicit ranges). Try `re.compile` on each under Python 3.9 semantics; **skip and report** rules that still fail. The generator prints `compiled N of M` and exits 1 if N is under 150.
- Apply a rule's `entropy` floor (Shannon entropy of the captured secret group) only when the rule defines one. Port the global `[allowlist]` regexes and stopwords.
- Add three local rules: env-style lines `(?m)^\s*(?:export\s+)?[A-Z][A-Z0-9_]{2,}\s*=\s*(\S{8,})` only when the key name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, or `CREDENTIAL`; base64 or hex runs of 40+ chars with entropy above 4.0; credentials in URLs `\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]+)@`.
- **Startup cost matters.** `_redact_rules.py` stores pattern strings. `redact()` lowercases the text once, runs only rules with a keyword present (rules with no keywords always run), and compiles each pattern lazily with a module-level dict cache. Importing `redact` must compile nothing.
- Corpus `tests/fixtures/secrets_corpus.jsonl`: about 200 positives (`{"text": ..., "secret": ..., "family": ...}`) generated synthetically across at least 15 families (AWS, GitHub, Slack, Stripe, OpenAI, Anthropic, OpenRouter, Google API, JWT, private key block, npm, PyPI, database URL, env assignment, generic high-entropy) embedded in realistic tool-output context, and about 200 hard negatives (`{"text": ..., "secret": null}`): git SHAs, UUIDs, lockfile integrity hashes, base64 image fragments, file paths, ordinary prose, hex colors. Every positive is fake and format-valid; none may be a real credential. Generate it with a seeded script section in `gen_redact.py --corpus` so it is reproducible.

**Required tests:**
- normalize: zero-width and bidi characters removed and counted; ANSI color codes removed; NFKC folds full-width letters.
- truncate: short text untouched; long text keeps exact head and tail and reports the right N; an error line placed in the last 100 chars survives 500 KB of padding before it.
- redact: each local rule catches its case; an ordinary sentence yields zero hits; idempotent; the secret string itself is absent from the output.
- **Gate G1.7** (`tests/test_redaction_gate.py`): over the corpus, recall = positives whose `secret` no longer appears in the output, false-positive rate = negatives with any hit. Assert recall at least 0.95 and FPR at most 0.02. Print both numbers.
- Performance: `redact` on an 8,192-char typical build log takes under 15 ms median over 20 runs (mark `slow`); `python -X importtime` style check that importing `verdict_hot.redact` stays under 25 ms on the dev interpreter.
- Hypothesis property: for arbitrary text, `redact` never raises and output length is bounded by `len(text) + 40 * hits`.

- [ ] Steps: vendor the file and license; write tests; write `gen_redact.py`; run `make gen-redact`; implement `textnorm` and `redact`; iterate on translation until G1.7 passes; run `make sync-hot && make check && make test`; commit `feat: add text normalization and a measured secret redactor`.

If recall cannot reach 0.95 after translating the vendored rules and adding the three local rules, stop and report the per-family miss table instead of weakening the corpus.

---

### Task 3: Policy file, never-send check, evidence tags, claim extraction

**Files:** `plugin/policies/default.json`, `schemas/policy-v1.json`, `verdict_hot/policy.py`, `verdict_hot/gates.py`, `verdict_hot/claims.py`, tests `tests/unit/test_policy.py`, `test_gates.py`, `test_claims.py`.

**Interfaces (Produces):**
- `default.json` keys: `policy_version` ("2026.09.1"), `mode` ("shadow"), `store` {`retention_days`: 45, `excerpt_head`: 4096, `excerpt_tail`: 4096, `input_excerpt_max`: 300, `error_head`: 300, `error_tail`: 2000, `prompt_max_chars`: 6000, `final_message_max_chars`: 8000}, `never_send` {`path_globs`: [...PLAN 5.1 list...], `bash_patterns`: [...]}, `checks` {`runner_patterns`: [...], `extra`: []}, `soft_failure` {`output_patterns`: [...], `masking_patterns`: [...], `http_error_patterns`: [...], `tools`: ["Bash", "Agent", "WebFetch"]}, `claims` {`success_verbs`: [...], `max_claims`: 6}. Every pattern list comes from PLAN sections 5.0 and 5.1 verbatim.
- `policy.load_policy(path: Path | None = None) -> Policy` (frozen dataclass tree). Resolution in M1: explicit path, else `verdict_home() / "policy.json"` if it exists, else the packaged default located relative to `__file__` (`../../policies/default.json` in the plugin; the CLI copy resolves the packaged default through a `VERDICT_POLICY_DEFAULT` override or a copy placed by `sync_hot.py` at `src/agent_verdict/verdict_hot/default_policy.json`). Invalid JSON or a missing required key raises `PolicyError`; callers on the hot path catch it and fall back to the packaged default.
- `gates.is_never_send(tool_name: str, tool_input: Mapping[str, object], policy: Policy) -> bool`: glob match on `file_path` (supporting `**`, `~` expansion against the real home, case-sensitive) and regex match on Bash `command`.
- `gates.is_check(command: str, policy: Policy) -> bool`; `gates.is_soft_fail_candidate(tool_name: str, command: str | None, output: str, policy: Policy) -> bool`.
- `claims.extract_claims(message: str, policy: Policy) -> tuple[str, ...]`: sentence split on `.`, `!`, `?`, newlines and list bullets; keep sentences containing a success verb as a whole word (case-insensitive); strip markdown emphasis; prefer sentences mentioning test, build, lint, type check, fix; cap at `max_claims`; each claim at most 240 chars.

**Required tests:** the packaged policy validates against `schemas/policy-v1.json` (hand-written validator); never-send catches `.env`, `config/.env.local`, `~/.ssh/id_ed25519`, `server.pem`, and `cat .env`, and does not catch `src/environment.py` or `README.md`; `is_check` recognizes `pytest -q`, `npm test`, `cargo test`, `make check`, `uv run mypy`, and rejects `echo pytest`? (decide by pattern anchoring: the runner must start a shell segment, so `echo pytest` is false while `cd app && pytest` is true); soft-fail candidates: output containing `3 failed, 12 passed`, a `Traceback`, a command ending in `|| true`, `npm test | tail -5`, an `HTTP/1.1 500` body; a plain `ok` output is false; tools outside `soft_failure.tools` are always false; claims: the stop fixture's message and five hand-written messages, including one with no claims, a bulleted list, and a message with eight claims (capped at six with test claims first).

- [ ] Steps: tests first; implement; `make sync-hot && make check && make test`; commit `feat: add policy file, never-send check, evidence tags, and claim extraction`.

---

### Task 4: Parsers and recorders

**Files:** `verdict_hot/parsers.py`, `verdict_hot/recorders.py`, tests `tests/unit/test_parsers.py`, `tests/unit/test_recorders.py`.

**Interfaces (Produces):**
- `parsers.parse_event(payload: Mapping[str, object]) -> HookEvent`: dispatches on `hook_event_name`. Frozen dataclasses `Common(session_id, prompt_id, agent_id, agent_type, permission_mode, cwd, hook_event_name)`, `SessionStartEvent(common, source, model)`, `PromptEvent(common, prompt)`, `PostEvent(common, tool_name, tool_use_id, tool_input, tool_response, duration_ms, mcp_server)`, `PostFailEvent(common, tool_name, tool_use_id, tool_input, error, is_interrupt, duration_ms)`, `StopEvent(common, stop_hook_active, last_assistant_message, background_tasks_n)`, `SessionEndEvent(common, reason)`. A missing required field or wrong type raises `ParseError(field)`. Unknown extra fields are ignored. Optional everywhere: `prompt_id`, `agent_id`, `agent_type`, `permission_mode`, `duration_ms`, `model`, `mcp_server`, `scratchpad_dir`, `effort`.
- `parsers.parse_exit_code(error: str) -> int | None`: only when the first line matches `^Exit code (\d+)$` (A2, G11).
- `recorders.build_row(event: HookEvent, policy: Policy, now: float) -> dict[str, object]` (pure): common fields per PLAN 4.3 plus `ts` (ISO 8601 UTC from `now`), `schema_v`, `plugin_version`, `event` in `session_start|prompt|post|post_fail|stop|session_end`, and `cwd_hash` = first 16 hex chars of `sha256(cwd)` (superseded by task-6-brief.md item 2: FNV-1a 64-bit formatted as 16 hex chars, to drop the `hashlib`/`_hashlib` C-extension import -- ~14ms of per-process startup -- from every event; `cwd_hash` is a grouping key, not a security boundary, so this is acceptable per global-constraints.md). Event fields exactly as PLAN 4.3 lists for M1 (`stop` rows carry `stop_hook_active`, `final_message_excerpt`, `claims`, `background_tasks_n`; no `gate_reason`, no `arm`).
- Text pipeline for every free-text field, in this order: never-send check (if true, the row keeps structure but every excerpt becomes `"[never-send]"` and `never_send: true`), `normalize`, `redact`, `truncate_anchored` with the policy limits; rows carry `redaction_hits` and `sanitized_chars` totals. Bash `out_head`/`out_tail` come from `stdout` and `stderr` joined as `stdout + "\n[stderr]\n" + stderr` when stderr is non-empty; non-Bash tools serialize `tool_response` with `json.dumps(sort_keys=True)`. `input_excerpt` is the Bash `command`, the `file_path` for Write, Edit and NotebookEdit, the `url` for WebFetch, the `description` or `prompt` head for Agent, else compact JSON.
- `recorders.record(payload: Mapping[str, object]) -> str`: parse, build, `ledger.append_row`; returns the outcome string for `hook.log`.

**Required tests:** every committed fixture for the six M1 events parses and produces a row that validates against `schemas/ledger-v1.json`; the failure fixture yields `status: "error"`, `exit_code: 3`, `is_interrupt` as captured; `parse_exit_code` returns None for `"Command timed out"` and for `"note\nExit code 1"`; the SessionStart fixture (no `prompt_id`) yields `"prompt_id": null` present in the row; a payload containing a fake `sk-or-v1-...` token in Bash stdout produces a row without that token; a Write to `.env` yields `never_send: true` and no file content; `build_row` does not mutate its input (deep-compare before and after); Hypothesis: `parse_event` on arbitrary JSON-like dicts either returns an event or raises `ParseError`, never anything else.

- [ ] Steps: tests first; implement; `make sync-hot && make check && make test`; commit `feat: add strict hook parsers and ledger recorders`.

---

### Task 5: Entry point, launcher, plugin packaging, SSL context

**Files:** `plugin/hooks/verdict_hook.py`, `plugin/hooks/run.sh` (0755), `plugin/hooks/hooks.json`, `plugin/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `verdict_hot/sslctx.py`, tests `tests/integration/test_hook_cli.py`, `tests/unit/test_sslctx.py`, `tests/test_plugin_manifests.py`.

**Interfaces (Produces):**
- `run.sh <event>`: `[ -n "${VERDICT_DISABLE:-}" ] && exit 0`; unset `PYTHONPATH PYTHONHOME PYTHONSTARTUP`; interpreter = `$VERDICT_PYTHON`, else the path in `${VERDICT_HOME:-$HOME/.verdict}/interpreter` if that file exists and the path is executable, else `command -v python3`, else exit 0; `exec "$py" -S "$(dirname "$0")/verdict_hook.py" "$@"`.
- `verdict_hook.main(argv) -> int`: checks `VERDICT_DISABLE` and `os.name == "nt"` first; installs the excepthook; reads stdin (cap 5 MB); `json.loads`; calls `recorders.record`; logs the invocation with `total_ms`; **always returns 0 and prints nothing to stdout**, including on malformed stdin, unknown events, and an unwritable data root. It enforces a 200 ms soft deadline by skipping redaction-heavy work only if already past it (record `outcome: "skipped"`); simplest compliant approach: measure and log, never sleep or retry.
- `hooks.json`: six events per the Global Constraints, `timeout` 5 (none on SessionEnd), exec form `{"type":"command","command":"${CLAUDE_PLUGIN_ROOT}/hooks/run.sh","args":["<event-slug>"]}`; the entry point trusts `hook_event_name` from the payload, the arg is informational.
- `plugin.json`: `name` "agent-verdict", explicit `version` "0.1.0", `description`, `author`, `userConfig` with `mode` (options `shadow`, `enforce`, `off`; default `shadow`) only. No `api_key` field until M2, because v0.1 is key-free.
- `marketplace.json` at the repo root: `name` "agent-verdict", `owner`, one plugin with `source` "./plugin".
- `sslctx.build_context() -> ssl.SSLContext` and `sslctx.pick_ca_bundle(candidates, exists)`, same behavior as `scripts/smoke_jev.py` (D-012). After this task `scripts/smoke_jev.py` stays independent; do not import across.

**Required tests (integration, subprocess, temp `VERDICT_HOME`):** for each of the six fixture families, `run.sh` exits 0, stdout is empty, and exactly one valid row lands in the session file; malformed stdin exits 0 with an `exception` line in `hook.log` and no ledger row; `VERDICT_DISABLE=1` writes nothing at all; a sentinel value in `CLAUDE_PLUGIN_OPTION_API_KEY` never appears in the ledger or `hook.log` even when the payload makes the recorder raise; the same tests pass with `VERDICT_PYTHON=/usr/bin/python3` when that path exists (skip otherwise); an `interpreter` file pointing at a missing path falls through to `python3`; file modes are 0600 and 0700. Manifest test: both JSON files parse, the hooks registered are exactly the six M1 events, every handler is exec form with `args`, and the PostToolUse matcher equals the constraint string.

- [ ] Steps: tests first; implement; run `claude plugin validate ./plugin --strict` (gate G1.3) and record the output; `make sync-hot && make check && make test`; commit `feat: add hook entry point, launcher, and plugin manifests`.

---

### Task 6: CLI (`stats`, `doctor`) and the latency and end-to-end gates

**Files:** `src/agent_verdict/cli.py` (modify), `stats.py`, `doctor.py`, `scripts/bench_hook.py`, `scripts/e2e_cheap.py`, tests `tests/unit/test_stats.py`, `tests/unit/test_doctor.py`, `tests/test_bench_hook.py`; modify `Makefile` (`bench-hook`, `e2e-cheap`, `plugin-validate`, `doctor`).

**Interfaces (Produces):**
- `verdict stats [--json] [--count]`: reads the JSONL directly. Reports sessions, prompts, tool rows, failure rows, stops, stops with at least one claim, never-send rows, redaction hits, rows per event, and the date range. `--count` prints only the stop count.
- `verdict doctor [--audit] [--fix-interpreter] [--json]`: sections interpreter (path, version, which resolution step chose it), data root (exists, modes 0700 and 0600), plugin (whether `claude plugin list` mentions `agent-verdict`; "unknown" when the `claude` binary is missing), `hook.log` outcome counts for the last 7 days, provider TLS probe per host using `sslctx` with a 3 s timeout (**informational only**), key presence as the words `present` or `absent` (**informational only**). Exit code reflects only interpreter resolution and data-root modes (gate G1.6). `--audit` exits 1 if `hook.log` holds any `exception` outcome in the last 7 days. `--fix-interpreter` probes `VERDICT_PYTHON`, `python3` on PATH, `/usr/bin/python3`; keeps the first that runs `-S -c "import json,sqlite3,ssl"` within 1 s and completes a TLS handshake with `sslctx`; writes its absolute path to `verdict_home() / "interpreter"`.
- `scripts/bench_hook.py --n 40 [--python PATH]`: spawns `plugin/hooks/run.sh` per fixture family with a temp `VERDICT_HOME`, discards 5 warm-ups, prints p50 and p95 per event, exits 1 if any p50 is over 60 ms or any p95 over 120 ms (gate G1.2). Runs for the default interpreter and again for `/usr/bin/python3` when present.
- `scripts/e2e_cheap.py`: runs `claude -p "Run this exact shell command and tell me its exit code: sh -c 'exit 3'" --plugin-dir ./plugin --model haiku --max-turns 6 --permission-mode acceptEdits --allowedTools Bash --output-format stream-json --verbose --include-hook-events` with a temp `VERDICT_HOME` exported, then asserts: the stream shows the plugin loaded without plugin errors; the temp ledger holds a `post_fail` row with `exit_code == 3`, a `prompt` row, a `stop` row, and a `session_start` row; the stream text contains no `hook error`. Because the owner's global gate hook blocks the first Bash call of a session (G15), the assertion is on the presence of the row, not on call order. Exits non-zero with a clear message on any failed assertion (gate G1.4).

**Required tests:** `stats` over a synthetic ledger built with `ledger.append_row` gives exact counts, and `--json` is valid JSON; `doctor` exit code is 0 with no keys and an unreachable provider (monkeypatch the probe to raise), and 1 when the data root has mode 0755; `--audit` flips to 1 when an `exception` line exists; `--fix-interpreter` writes an absolute existing path; `bench_hook` percentile and threshold logic tested with injected timings (no subprocess).

- [ ] Steps: tests first; implement; run `make bench-hook`, `make e2e-cheap`, `make plugin-validate`, and `uv run verdict doctor` and paste outputs into the report; `make check && make test`; commit `feat: add stats, doctor, and the M1 latency and end-to-end gates`.

---

### Task 7: Consent, privacy, and README

**Files:** `docs/CONSENT.md`, `docs/PRIVACY.md`, `README.md`, `docs/DEVELOPING.md`.

- `docs/CONSENT.md`: one page per PLAN 9.1. In collector mode nothing leaves the machine. From M2, verification sends a redacted, truncated turn summary to the configured provider. Raw text stays local; only derived rows, numeric answers, and labels are shared, under MIT, in a public repository; the 45-day study retention window; how to pause (`VERDICT_DISABLE=1`), purge (delete the data root until `verdict purge` ships in M2), and revoke; do not join if your employer owns the code without written permission.
- `docs/PRIVACY.md`: the egress statement from PLAN 10, what the ledger stores, modes and retention, the never-send list, measured redaction numbers copied from the G1.7 test output, the seatbelt paragraph.
- `README.md`: what it is in three sentences, status "v0.1 collector: records locally, sends nothing", the two-command install using this repository as the marketplace, `verdict stats` and `verdict doctor`, kill switch, supported platforms (macOS and Linux; Windows disables itself), credit to jev-belay and a pointer to destructive_command_guard, link to `docs/PLAN.md`. No "first" or "only" claims.
- `docs/DEVELOPING.md`: the run-one-hook recipe, fixture capture, sync rule, gates and how to run them.

- [ ] Steps: write the four files using only facts present in `docs/PLAN.md`, `docs/VERIFIED_FACTS.md`, `docs/DECISIONS.md`, and the Task 2 and Task 6 reports; `make check && make test`; commit `docs: add consent, privacy, README, and developer guide for v0.1`.
