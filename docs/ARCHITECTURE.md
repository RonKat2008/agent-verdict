# Architecture

Condensed from `docs/PLAN.md` section 4. This file restates structure only; for rationale,
open decisions, and fact citations, follow the links below instead of duplicating them here.

- Full architecture writeup: `docs/PLAN.md` section 4.
- Platform and vendor facts (cited `A1`..`G15` etc.): `docs/VERIFIED_FACTS.md`.
- Why each choice was made, and what would reopen it: `docs/DECISIONS.md`.

Local-first. Full value with zero infrastructure beyond one API key. Collector mode needs no
key at all.

## Data flow

```
  Claude Code session
  ├─ SessionStart ─────▶ record session, local self-check (python, data dir, config)  (sync, no network)
  ├─ UserPromptSubmit ─▶ record redacted prompt under prompt_id          (sync, no network)
  ├─ PreToolUse ───────▶ rules gate: deny | ask | no decision            (sync, no network)
  ├─ PostToolUse ──────▶ record ok row, mark soft-fail candidates        (sync, no network)
  ├─ PostToolUseFailure▶ record error row with exit code                 (sync, no network)
  ├─ Stop ─────────────▶ evidence gate ─▶ (maybe) one Jev call ─▶ policy ─▶ pass | flag | block
  ├─ SubagentStop ─────▶ same verifier, record only by default
  └─ SessionEnd ───────▶ close session, bounded prune                    (1.5 s budget)
                │
                ▼  one stdlib-only Python process per event, about 25 to 40 ms
        plugin/hooks/verdict_hot/   ledger · redact · gates · state · provider · policy · guard
                │
     ┌──────────┴───────────────┐
     ▼                          ▼
  ~/.verdict/events/*.jsonl   SystemOne endpoint (TypeSafe or OpenRouter), Stop only
  append-only source of truth
     │
     ▼  verdict index  (derived, disposable; built in M3 for label, report, eval)
  ~/.verdict/index.db (stdlib sqlite3)
     │
     ├─ verdict stats | show | replay | doctor | purge | export | migrate   (read the JSONL directly)
     ├─ verdict label   (blinded terminal labeler)  ─▶ ~/.verdict/labels.jsonl
     ├─ verdict report  (self-contained HTML)
     └─ verdict eval    (metrics, baselines, tier gates) ─▶ reports/eval-<date>.md
```

## Components

| Component | Location | Tech |
|---|---|---|
| hot path | `plugin/hooks/verdict_hot/`, entry `plugin/hooks/verdict_hook.py`, launcher `plugin/hooks/run.sh` | Python 3.9+, stdlib only |
| plugin package | `plugin/` | `.claude-plugin/plugin.json`, `hooks/hooks.json`, `policies/default.json` |
| CLI and tools | `src/agent_verdict/` | Python 3.11+, stdlib core; extras `report` and `eval` |
| transcript adapter | `src/agent_verdict/adapters/transcript_v1.py` | stdlib, the only code that knows the transcript format |
| eval | `src/agent_verdict/eval/` | numpy, matplotlib, anthropic SDK, `typesafe-sdk` (extra `eval`) |
| post-launch | `hosted/`, `src/agent_verdict/dashboard/` | FastAPI, Postgres, static site (M6 only) |

See `docs/PLAN.md` 4.1 for what each component owns and why (`sync_hot.py`, the hot-tree diff
check, `mypy --strict`, etc.).

## Hook registration

| Event | Matcher | `hooks.json` timeout (s) | Internal deadline | Network |
|---|---|---|---|---|
| SessionStart | none | 5 | 200 ms | never |
| UserPromptSubmit | none | 5 | 200 ms | never |
| PreToolUse | `^(Bash\|Write\|Edit\|NotebookEdit)$` | 3 | 200 ms | never in v1 |
| PostToolUse | `^(Bash\|Write\|Edit\|NotebookEdit\|WebFetch\|Agent\|mcp__.*)$` | 5 | 200 ms | never |
| PostToolUseFailure | `*` | 5 | 200 ms | never |
| Stop | none | 15 | 2.5 s total | at most one request plus one retry |
| SubagentStop | `*` | 15 | 2.5 s | same as Stop |
| SessionEnd | `*` | none (shared 1.5 s budget) | 1.0 s | never |

Full launcher behavior (interpreter resolution, `-S` only, `VERDICT_DISABLE`, stale-interpreter
handling) is in `docs/PLAN.md` 4.2. See `docs/DECISIONS.md` D-008 and D-019 for why the matchers
and the launcher flags are what they are.

## Data root and model

`verdict_home()` returns `$VERDICT_HOME` if set, else `~/.verdict`. No code expands `~/.verdict`
directly. One file per session: `~/.verdict/events/<session_id>.jsonl`, directory mode 0700,
files 0600, one `os.write` per row under `flock`. Rows too new for the running code spool to
`~/.verdict/pending/`.

Common fields on every row: `schema_v`, `ts`, `event`, `session_id`, `prompt_id`, `agent_id`,
`plugin_version`, `permission_mode`. Per-event additional fields (`session_start`, `prompt`,
`pre`, `post`, `post_fail`, `stop`/`subagent_stop`, `verdict`, `action`, `session_end`) are listed
in `docs/PLAN.md` 4.3; that table is the source of truth for field names, not this file.

`hook.log`, `labels.jsonl`, and retention (45 days during the study, 14 at v1.0) are also
specified in `docs/PLAN.md` 4.3.

For what has actually been observed in real captured hook payloads, versus what the plan
documents, see `docs/VERIFIED_FACTS.md` section G.
