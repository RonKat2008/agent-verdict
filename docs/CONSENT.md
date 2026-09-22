# Consent

This page explains what `agent-verdict` (the "collector") records on your machine, what
happens to that data, and how to pause, stop, purge, or revoke it. Read it before you
install the plugin or before you agree to run it as a contributor to the study described
in `docs/PLAN.md`.

## What the collector records

On a Claude Code session where the plugin is enabled, `agent-verdict` records, into a
local ledger:

- Your prompts (`UserPromptSubmit`), redacted and truncated.
- The commands Claude Code runs and their tool outputs (`PostToolUse`), redacted and
  truncated to a head and a tail.
- Failures, including the exit code (`PostToolUseFailure`).
- The assistant's final message at the end of a turn (`Stop`), redacted and truncated.
- File paths touched by a tool call, but never file contents, except where a tool's own
  output happens to include them (and that output goes through the same redaction as
  everything else).

All of the above is redacted (see `docs/PRIVACY.md` for the redactor's measured recall
and false-positive rate) and truncated before it is written anywhere, including to your
own disk.

**Never-send list.** The contents and paths of certain files are never recorded or sent at
all: `.env*` files, PEM and key files, SSH keys and the `.ssh` directory, cloud credential
files (`~/.aws/credentials`, kubeconfig, service-account JSON, and similar),
package-manager credential files (`.npmrc`, `.netrc`, `.pypirc`, `.git-credentials`), and
Terraform state and variable files (`docs/PLAN.md` section 5.1, `docs/DECISIONS.md`
D-017). The check looks at every path-shaped field of a tool call, including the ones MCP
servers use, not only the built-in tools' `file_path`.

A match does not remove the row. The row is still written and marked `never_send: true`,
and it still records the tool name, the tool call's id (`tool_use_id`), whether the call
was a check (`is_check`), how long it took (`duration_ms`), and a hash of the working
directory (`cwd_hash`) — enough to know that a tool ran. Every piece of content and every
path on that row is replaced by the literal string `[never-send]`.

## What leaves this machine

**In this version, no recorded data leaves your machine.** `agent-verdict` v0.1 is a
collector: it writes to your local ledger under `~/.verdict`, and the recording path makes
no network calls at all. This is a deliberate design choice for M1, not a temporary
limitation of a beta.

One exception, which you trigger yourself and which sends none of your data: `verdict
doctor` opens a TLS connection to `openrouter.ai` and `api.typesafe.ai` to report whether
each provider is reachable. No ledger content and no API key is sent, but your machine's
IP address is exposed to those two hosts. Run `verdict doctor --no-probe` to skip the
check and make the command network-free.

**From M2 onward, this changes for verification.** When Verdict verifies a stop, it will
send a redacted, truncated summary of that turn (your prompt, the commands run, short
output excerpts, and the assistant's final message) to your configured provider
(TypeSafe or OpenRouter). You will be told about this switch, in the README and at
enable time, before it takes effect on your installation. Collector mode and local-only
mode will continue to send nothing.

Raw text never leaves the machine that produced it, in any version. What may eventually
be shared, under MIT, in this project's public repository, is derived data only: a
hashed event id, your contributor id, the hook kind, the tool name, a question key, a
label, an exit code, counts of error rows and claims, whether a check ran, numeric
answers, the model id that answered, and a hash of the raw row. Never the raw prompt,
command, output, or final message text.

## Where your data lives

Raw ledger data stays on your own disk, under `~/.verdict`. The directory is created
mode 0700 and its files mode 0600, set explicitly. During the study (through M4),
retention defaults to 45 days; the prune step never deletes an unlabeled session inside
that window, because collection starts before labeling does and the corpus is
irreplaceable.

## Controls

- **Pause.** Set `VERDICT_DISABLE=1` in your environment. The kill switch is checked
  before any other work and stops recording immediately.
- **Stop.** Uninstall the plugin (`/plugin uninstall agent-verdict`). Recording stops;
  your existing data stays under `~/.verdict` until you remove it yourself.
- **Purge.** Delete `~/.verdict` yourself. A `verdict purge` command is planned for M2
  and is not shipped in this version.
- **Revoke.** If you contributed data to the study and want your rows removed, message
  the maintainer. Your contributor rows are dropped in the project's next release.

## Before you join as a contributor

Do not install this plugin as a contributor to the study if your employer owns the code
you work on, unless you have their written permission. The collector records prompts,
commands, and tool output from your real sessions; if that work is not yours to share
even in derived form, do not opt in.

## The redactor's limits, stated plainly

The redactor is a best-effort regex tool, not a guarantee. Across four held-out probes run
by a reviewer against fresh secret material — a new set each round, kept from the person
writing the redactor — measured recall was **0.889, 0.967, 0.960, and 0.974**, with an
evidence-text false-positive rate of 0.011 on the fourth. That same fourth probe was later
**re-run** after the fixes made against it and measured 1.000 recall and 0.000
evidence-text false-positive rate; since the fixes targeted that exact set, it was no
longer held out, so treat the 1.000 as a regression check rather than a fresh result. All
of it is written down in
[`docs/measurements/redaction-heldout-2026-09-21.md`](measurements/redaction-heldout-2026-09-21.md).

Measured recall on fresh held-out sets is **0.96 to 0.97** on the last three probes:
assume roughly **1 in 30 secrets could survive** redaction and reach your local ledger.
Two limits are known and accepted: an all-lowercase hyphenated passphrase used as a
password value is not redacted, and a value shaped exactly like a Stripe *publishable*
key under an ordinary key name survives on purpose. Treat anything the collector might see
the same way you would treat a plaintext file on your own disk: if you would not want a
credential sitting there unencrypted, rotate it after it appears in a command or output,
whether or not you believe it was redacted.

If the redactor itself throws while processing a field, the row is still written with that
field replaced by `[redaction failed]`; the unredacted text is never written.

---

Signed: Ronit Katikaneni, maintainer of `agent-verdict`.
