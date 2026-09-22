# Privacy and Security

This page is the technical companion to `docs/CONSENT.md`. It describes what the ledger
stores, what modes exist, how retention works, and the measured limits of the redactor.
Facts here come from `docs/PLAN.md` section 10, `docs/DECISIONS.md` (D-017, D-021,
D-026, D-027), and the redaction gate results in
`.superpowers/sdd/2026-09-21-m1-collector/task-2-report.md`.

## Egress, stated honestly

From `docs/PLAN.md` section 10, quoted plainly:

> When Verdict verifies a stop, it sends a redacted, truncated summary of that turn
> (your prompt, the commands run, short output excerpts, and the assistant's final
> message) to your configured provider. Collector mode and local-only mode send
> nothing.

In this version (v0.1, collector mode), no verification step exists yet, so this hook
never fires and no network call is made from the recording path.

## What the ledger stores per event

One JSONL file per session lives at `~/.verdict/events/<session_id>.jsonl`. Every row
carries `schema_v`, `ts`, `event`, `session_id`, `prompt_id`, `agent_id`,
`plugin_version`, and `permission_mode`. Per event type:

| Event | Additional fields |
|---|---|
| `session_start` | `source`, `model` (optional), `cwd_hash`, `cc_effort` |
| `prompt` | `prompt_excerpt` (redacted, at most 1,500 tokens), `redaction_hits` |
| `pre` | `tool_use_id`, `tool_name`, `rule_id`, `decision` (`deny`, `ask`, or null), `never_send` (bool) |
| `post` | `tool_use_id`, `tool_name`, `input_excerpt` (redacted, at most 300 chars), `out_head` and `out_tail` (redacted, at most 4,096 chars each), `raw_bytes`, `duration_ms`, `is_check`, `soft_fail_candidate`, `mcp_server`, `redaction_hits`, `sanitized_chars` |
| `post_fail` | `tool_use_id`, `tool_name`, `input_excerpt`, `status: "error"`, `exit_code` (int or null), `is_interrupt`, `error_excerpt` (first 300 plus last 2,000 chars, redacted), `duration_ms` |
| `stop`, `subagent_stop` | `stop_hook_active`, `final_message_excerpt` (redacted, at most 2,000 tokens), `claims[]`, `background_tasks_n` |
| `session_end` | `reason` |

A `pre` row with no matching `post` or `post_fail` row means the call was denied or
never ran; it is never counted as a failure. `verdict` and `action` rows (verification
answers and gate decisions) arrive in M2 and are not written in v0.1.

## Modes and the kill switch

Three modes, set through the plugin's `mode` option: `shadow` (default; records and
judges, never blocks or nudges), `enforce` (may block on strong evidence, not available
until a later version), and `off` (disables recording entirely). `VERDICT_DISABLE=1` is
a kill switch checked before any other work in every hook, regardless of mode.

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
and variable files, plus Bash commands that read, copy, or print any of them. This list
runs first, ahead of the redactor, and is not itself measured for recall because it is a
deterministic path match, not a content classifier.

## Redaction design and measured numbers

One `redact()` function, generated from a pinned gitleaks rule snapshot plus local
entropy and assignment rules, runs at every point text could leave the process boundary:
before a ledger write, before a provider request (from M2), and before an export. A
redactor exception fails closed: nothing is sent or written on an unexpected error.

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

Measured numbers, on the project's own corpus
(`.superpowers/sdd/2026-09-21-m1-collector/task-2-report.md`): recall 0.9861,
evidence-text false-positive rate 0.0000, opaque-token over-redaction 0.2917. On a
reviewer's independent held-out probes, recall measured 0.889, 0.967, 0.960, and 0.974
across four rounds, with a final held-out pass at recall 1.000, evidence-text
false-positive rate 0.000, and opaque-token over-redaction 0.27.

Caveats, stated plainly:

- The redactor is a best-effort regex tool over a pinned gitleaks snapshot plus local
  rules, not a general secret detector. It is blind to secret formats it has never seen
  and to secrets that read as ordinary words (a password that is itself a dictionary
  word or a short phrase can pass through).
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
