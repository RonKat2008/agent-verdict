# Upgrading

## v0.1 → v0.2

No ledger schema change: `schema_v` stays `1`. Every field M2 adds (`pre`, `subagent_stop`,
`verdict`, and `action` rows; the `is_check`/`soft_fail_candidate` and evidence-gate
fields M1 already carried) is additive, per `docs/PLAN.md`'s "every new row type is added
to `schemas/ledger-v1.json`... with `schema_v` unchanged" rule. Existing v0.1 ledger
files under `~/.verdict/events/` need no migration and remain readable as-is.

### What actually changes for you

- **Verification now calls a real provider.** From v0.2, the Stop and SubagentStop hooks
  send a redacted turn summary to your configured provider whenever the evidence gate
  decides a stop needs judging. v0.1 never did this — recording was the whole plugin.
  Read `docs/PRIVACY.md`'s "What changed in v0.2" section before upgrading if this
  matters to you: it lists exactly what is (and is not) sent.
- **Two new plugin options: `provider` and `api_key`.** `provider` selects which System
  One preset judges a stop (`openrouter` default, `typesafe`, or `local-only` to disable
  verification's network call entirely while still recording and gating locally).
  `api_key` supplies the key directly through the plugin's own settings UI; if left
  unset, the hook falls back to the `OPENROUTER_API_KEY` or `TYPESAFE_API_KEY`
  environment variable (matching the selected provider). Neither option's value is ever
  logged or written to the ledger.
- **Two new hooks are registered: `PreToolUse` and `SubagentStop`.** `PreToolUse` runs a
  deterministic, network-free rules gate ahead of `Bash`/`Write`/`Edit`/`NotebookEdit`
  calls (see the README's "PreToolUse rules gate" section) and records a `pre` row.
  `SubagentStop` runs the same completion verifier a top-level `Stop` does, but only
  actually blocks when `subagent_block` is set in the policy; otherwise it records
  `would_have` the same way `shadow` mode does everywhere else.
- **`mode: enforce` is now meaningful.** In v0.1, `enforce` had no verifier behind it to
  drive; from v0.2 it can actually block a Stop (`{"decision": "block", "reason": ...}`)
  on strong evidence, at most `max_blocks_per_prompt` times per prompt. `shadow` (the
  default) is unchanged: it still only records what `enforce` would have done.
- **New CLI commands.** `verdict show`, `verdict replay`, `verdict purge`,
  `verdict export --goldset`, and `verdict policy lint` ship in v0.2, alongside v0.1's
  `verdict stats` and `verdict doctor`. See the README's "The `verdict` CLI" table.

### How to set the key

Either set it once through the plugin's own configuration UI (`api_key`, marked
sensitive so Claude Code does not echo it back), or export it in your shell before
starting Claude Code:

```
export OPENROUTER_API_KEY="sk-or-v1-..."   # for provider: openrouter (the default)
export TYPESAFE_API_KEY="..."               # for provider: typesafe
```

With no key set for the selected provider, the Stop/SubagentStop hooks record an
`action` row with `gate_reason: "no_key"` and exit 0 with empty stdout — never a visible
error, and never a block. Nothing about upgrading requires a key: leaving `provider` at
its default with no key configured behaves like `provider: local-only` for the
verification step, minus the explicit opt-out record.

### If you want v0.1's exact behavior

Set the plugin's `provider` option to `local-only`, or `mode` to `off`. Both fully
replicate v0.1: no request to any provider is ever built, and `mode: off` stops
recording entirely, exactly as it did in v0.1.
