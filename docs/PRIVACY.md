# Privacy and Security

This page is the technical companion to `docs/CONSENT.md`. It describes what the ledger
stores, what modes exist, how retention works, and the measured limits of the redactor.
Facts here come from `docs/PLAN.md` section 10, `docs/DECISIONS.md` (D-017, D-021,
D-026, D-027), and the redaction measurements in
[`docs/measurements/redaction-heldout-2026-09-21.md`](measurements/redaction-heldout-2026-09-21.md).

## Egress, stated honestly

From `docs/PLAN.md` section 10, quoted plainly:

> When Verdict verifies a stop, it sends a redacted, truncated summary of that turn
> (your prompt, the commands run, short output excerpts, and the assistant's final
> message) to your configured provider. Collector mode and local-only mode send
> nothing.

In this version (v0.1, collector mode), no verification step exists yet, so this hook
never fires and **the recording path makes no network call at all**: nothing under
`plugin/hooks/` opens a socket.

One command in this package does reach the network, and only when you run it yourself:
`verdict doctor` opens a TLS connection to `openrouter.ai` and `api.typesafe.ai` to report
whether each provider is reachable. That probe sends no ledger data and no API key, but it
does expose your machine's IP address to those hosts. It is informational only, and
`verdict doctor --no-probe` skips it entirely.

## What the ledger stores per event

One JSONL file per session lives at `~/.verdict/events/<session_id>.jsonl`. Every row
carries `schema_v`, `ts`, `event`, `session_id`, `prompt_id`, `agent_id`,
`plugin_version`, and `permission_mode`. Per event type:

| Event | Additional fields |
|---|---|
| `session_start` | `source`, `model` (optional), `cwd_hash`, `cc_effort` |
| `prompt` | `prompt_excerpt` (redacted, at most 6,000 characters), `redaction_hits` |
| `pre` (from M2) | `tool_use_id`, `tool_name`, `rule_id`, `decision` (`deny`, `ask`, or null), `never_send` (bool) |
| `post` | `tool_use_id`, `tool_name`, `input_excerpt` (redacted, at most 300 chars), `out_head` and `out_tail` (redacted, at most 4,096 chars each), `raw_bytes`, `duration_ms`, `is_check`, `soft_fail_candidate`, `mcp_server`, `redaction_hits`, `sanitized_chars` |
| `post_fail` | `tool_use_id`, `tool_name`, `input_excerpt`, `status: "error"`, `exit_code` (int or null), `is_interrupt`, `error_excerpt` (first 300 plus last 2,000 chars, redacted), `duration_ms` |
| `stop` (and `subagent_stop`, from M2) | `stop_hook_active`, `final_message_excerpt` (redacted, at most 8,000 characters), `claims[]`, `background_tasks_n` |
| `session_end` | `reason` |

M1 writes only `session_start`, `prompt`, `post`, `post_fail`, `stop`, and `session_end`
rows. `pre` and `subagent_stop` are listed above because the schema already carries them,
but no v0.1 hook emits either; from M2, a `pre` row with no matching `post` or `post_fail`
row will mean the call was denied or never ran, and is never counted as a failure.
`verdict` and `action` rows (verification answers and gate decisions) also arrive in M2.
Every excerpt cap above is a count of **characters**, not tokens.

## Modes and the kill switch

Three modes, set through the plugin's `mode` option: `shadow` (default; records and
judges, never blocks or nudges), `enforce` (may block on strong evidence, not available
until a later version), and `off` (disables recording entirely). `off` is checked in the
hook entry point before anything is imported, so it writes nothing at all — not even a
`hook.log` line. Setting `"mode": "off"` in `~/.verdict/policy.json` has the same effect
on the ledger, one step later: the hook runs, writes no row, and logs the invocation as
`skipped`. `VERDICT_DISABLE=1` is a kill switch checked before any other work in every
hook, regardless of mode.

## Retention

`store.retention_days` defaults to 45 during the study (M1 through M4) and drops to 14
from v1.0. The prune step never deletes a session inside the retention window that has
unlabeled stops, or one whose stops appear in the frozen goldset, because the study
corpus is collected well before labeling starts and cannot be replaced if pruned early
(D-021).

## Never-send list, summarized

A deterministic check runs before anything is recorded or sent. A match means the
content is not written to the ledger and never reaches a provider. It covers `.env*`
files, PEM and key files, SSH private keys and the `.ssh` directory, cloud and CI
credential files (AWS, kubeconfig, service-account JSON, Docker config), package-manager
credential files (`.npmrc`, `.netrc`, `.pypirc`, `.git-credentials`), and Terraform state
and variable files, plus Bash commands that read, copy, or print any of them. The path
check reads every path-shaped field a tool call carries, not just `file_path`: `path`,
`paths`, `uri`, `file:` URLs, `filename`, `file`, `notebook_path`, `target`, `source`,
`destination`, and — for MCP tools, whose schemas are not known in advance — any top-level
string value that looks like a filesystem path. This list runs first, ahead of the
redactor, and is not itself measured for recall because it is a
deterministic path match, not a content classifier.

A match does not delete the row. The row is still written, marked `never_send: true`, and
still carries the tool name, the `tool_use_id`, `is_check`, `duration_ms`, and `cwd_hash`
— enough to know that a tool ran and roughly how long it took. What it does not carry is
any content or path: every text excerpt on the row (the input excerpt, the output head and
tail, the error excerpt) is the literal string `[never-send]`, and a failing never-send row
records no exit code.

## Redaction design and measured numbers

One `redact()` function, generated from a pinned gitleaks rule snapshot plus local
entropy and assignment rules, runs at every point text could leave the process boundary:
before a ledger write, before a provider request (from M2), and before an export.

A redactor exception fails closed **on the text, not on the row**: the row is still
written, but the affected field is replaced by the literal string `[redaction failed]` and
the row records `redaction_failed: true`. The original text is never written in that case,
so a redactor bug costs you the excerpt, never the secret inside it.

The gate (`docs/DECISIONS.md` D-026, amended by D-027) splits false positives by harm,
because a single blended number hid two very different failure modes:

- **Evidence-text false positives** are the harmful kind: redacting prose, error
  messages, commands, paths, or identifiers made of words damages the ledger's ability
  to show what actually happened. This rate is gated at 0.02 or below.
- **Opaque-token over-redaction** is by design and reported, not gated: a random-looking
  string of 20 or more characters with no word structure (nonces, cursors, request ids,
  digests, binary blobs) is statistically indistinguishable from a real secret using
  text alone, so the redactor errs toward hiding it. Redacting an opaque token removes
  no evidence a verifier or a labeler actually reads.

Measured numbers are collected in
[`docs/measurements/redaction-heldout-2026-09-21.md`](measurements/redaction-heldout-2026-09-21.md).

On the project's own corpus (the regression suite the implementer can see): recall 0.9861,
evidence-text false-positive rate 0.0000, opaque-token over-redaction 0.2917.

On a reviewer's independent held-out probes — fresh secret material, a new set each round,
kept from the implementer — recall measured **0.889, 0.967, 0.960, and 0.974** across four
rounds, with evidence-text false-positive rate 0.011 on the fourth. After the last two fix
rounds, that same fourth probe was **re-run** and measured recall 1.000 and evidence-text
false-positive rate 0.000; because the fixes were made against it, it was no longer held
out, and that 1.000 is a regression check, not a held-out result.

**Take the held-out band as the real number: measured recall on fresh held-out sets is
0.96 to 0.97 on the last three probes. Assume roughly 1 in 30 secrets could survive
redaction and reach your local ledger.** Evidence-text false positives measured 0.000 to
0.011 on the last two measurements, and opaque-token over-redaction about 0.27 to 0.29:
Verdict over-redacts random-looking strings by design.

Caveats, stated plainly:

- The redactor is a best-effort regex tool over a pinned gitleaks snapshot plus local
  rules, not a general secret detector. It is blind to secret formats it has never seen
  and to secrets that read as ordinary words (a password that is itself a dictionary
  word or a short phrase can pass through).
- Two residuals are known and accepted, because both are indistinguishable from harmless
  text by construction (D-027's discussion): an all-lowercase hyphenated passphrase used
  as a password value (`password: correct-horse-battery-staple`) is not redacted, and a
  value that fully matches Stripe's *publishable*-key shape under a key name that is not
  password-like survives, since publishable keys are public by design and redacting them
  measured as an evidence-text false positive.
- It is a second line of defense, behind the never-send list and behind the fact that
  the data it does write stays in a private, mode-restricted local store rather than
  being transmitted anywhere in this version.
- Because recall is measured, not perfect, treat anything the collector might see the
  way you would treat a plaintext file on your own disk, and rotate credentials that
  pass through a recorded command or output.

## The seatbelt paragraph

`agent-verdict` is a seatbelt, not a sandbox. Hooks are best-effort: Claude Code's own
documentation says a hook that times out does not block, and recommends using the
permission system, not a hook, to enforce a hard allow or deny. Keep your own `deny`
rules in place, and consider installing `destructive_command_guard` alongside this
plugin for broad, rule-based command blocking that does not depend on a hook completing
in time.

## Hook logs

`~/.verdict/hook.log` records one line per hook invocation: a timestamp, the event, the
session id, an outcome (`ok`, `skipped`, `timeout`, `provider_error`, `exception`,
`disabled`), an error class name, and timing fields. It never contains tool content or
key material. Every string written to it passes a scrub step that drops
`CLAUDE_PLUGIN_OPTION_*` values, `Authorization` headers, and key-shaped tokens, and the
process's exception hook is replaced so no raw traceback reaches the log.
