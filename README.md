# agent-verdict

`agent-verdict` is a Claude Code plugin that records what happened during a session:
which commands ran, whether they succeeded or failed, and what the assistant said at the
end of the turn. It writes this into a local, append-only ledger on your own machine. At
Stop and SubagentStop it also verifies the assistant's final message against that
ledger, sending a redacted evidence summary — never file contents, never raw tool
output — to the selected provider for judging. It does this through Claude Code's own
hooks, with no separate service to run. Recording itself makes no network calls at all;
verification is the one part of this plugin that does, and `provider: local-only` turns
it off (recording continues; nothing is ever sent). `verdict doctor` also touches the
network, only to check provider reachability — see below.

**Status: M2 shadow verifier. `mode: shadow` (the default) records what enforce mode
would have done but never blocks or prints anything; `mode: enforce` can block a Stop
on strong evidence.**

## Install

This repository is currently private. Once it is public, or if you have collaborator
access, install with two commands inside Claude Code:

```
/plugin marketplace add RonKat2008/agent-verdict
/plugin install agent-verdict@agent-verdict
```

## Supported platforms

macOS and Linux. On Windows, the hook records a `disabled` outcome and exits
immediately; nothing is recorded there yet.

## Checking the collector

`verdict stats` summarizes the local ledger:

```
$ uv run verdict stats
sessions: 3
prompts: 12
tool rows: 41
failure rows: 4
stops: 12
stops with a claim: 9
never-send rows: 0
redaction hits: 2
rows per event:
  post: 41
  post_fail: 4
  ...
date range: 2026-09-21T14:02:11+00:00 .. 2026-09-21T18:47:03+00:00
```

`verdict doctor` diagnoses the local environment: interpreter resolution, data
directory, plugin registration, provider reachability, and key presence. It exits 0 with
zero provider keys configured; the provider and key lines are informational only.

The reachability line is the only network call anywhere in this version: it opens a TLS
connection to `openrouter.ai` and `api.typesafe.ai`, sending no ledger data and no API
key, but exposing your machine's IP address to those two hosts. Run `verdict doctor
--no-probe` to skip it and keep the command entirely offline. Example, captured on the
development machine:

```
$ uv run verdict doctor
interpreter: /Users/ronitkatikaneni/Projects/VERDICT/.venv/bin/python3 (via python3 on PATH, version 3.12.0)
data root: /Users/ronitkatikaneni/.verdict (exists: False)
plugin registration: not registered
hook.log outcomes (7d): {}
tls openrouter.ai: reachable (informational)
tls api.typesafe.ai: reachable (informational)
key OPENROUTER_API_KEY: absent (informational)
key TYPESAFE_API_KEY: absent (informational)
key ANTHROPIC_API_KEY: absent (informational)
```

## Kill switch

Set `VERDICT_DISABLE=1` in your environment to stop all recording immediately. It is
checked before any other work, in every hook.

The plugin's `mode` option does the same thing persistently: `mode: off` is read before
anything else is imported, so nothing at all is written — not even a hook log line.
`shadow` (the default) records everything, including what the Stop verifier would have
done, but never blocks or prints anything. `enforce` can actually block a Stop on strong
evidence (PLAN.md 5.3's R1–R4 rules) or leave a non-blocking note on weaker evidence.

## Measured overhead

Per-event latency through the hook launcher, measured on the development machine with
5 warm-up runs discarded (`.superpowers/sdd/2026-09-21-m1-collector/task-6-perf-report.md`,
gate D-029): the `post-fail` event measured p50 58 ms on Python 3.14, and 70 ms on
Apple's bundled `/usr/bin/python3` (3.9). Both numbers include a bare-interpreter start
floor of about 21 ms (3.14) and 31 ms (3.9) measured the same way; the fixed cost of
starting a Python process accounts for most of the total. Numbers vary with machine
load and are not a guarantee for your hardware.

## How it works

Claude Code fires hooks at points in a session: when it starts, when you submit a
prompt, after a tool call succeeds or fails, and when the assistant stops. `agent-verdict`
registers a small, synchronous, standard-library-only Python process on each of those
events, which redacts and truncates whatever text it sees and appends one row to a local
JSONL ledger under `~/.verdict`. Nothing is judged yet in this version; v0.1 only
records. From M2, a verification step will read the ledger at `Stop`, decide whether
there is enough evidence to check, and (only then) send a redacted, truncated summary to
a configured model provider to judge whether the assistant's final message matches what
actually happened. See `docs/PRIVACY.md` and `docs/CONSENT.md` for exactly what that
step will and will not send, and `docs/PLAN.md` for the full design.

## Prior art

The Stop-time evidence-gating idea in this project's design builds on prior art:
[`jev-belay`](https://github.com/valentynkit/jev-belay) by `valentynkit`, which uses
regex evidence gates ahead of a model call to check whether a session's final message is
supported by what actually ran. For rule-based blocking of destructive commands, see
[`destructive_command_guard`](https://github.com/Dicklesworthstone/destructive_command_guard),
which this project's README recommends installing alongside for broad command-safety
coverage that does not depend on a model call.

## Further reading

- [`docs/PLAN.md`](docs/PLAN.md): the full design and milestone plan.
- [`docs/PRIVACY.md`](docs/PRIVACY.md): what is stored, what is (and is not yet) sent,
  and the redactor's measured numbers.
- [`docs/measurements/redaction-heldout-2026-09-21.md`](docs/measurements/redaction-heldout-2026-09-21.md):
  the held-out redaction probes behind those numbers.
- [`docs/CONSENT.md`](docs/CONSENT.md): what a contributor to the study is agreeing to,
  and how to pause, stop, purge, or revoke.
