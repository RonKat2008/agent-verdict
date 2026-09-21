# Verified Facts

Platform and vendor facts this project depends on, each checked against a live source on **2026-09-20**.
This file outranks `docs/PLAN.md` wherever they disagree. `docs/DECISIONS.md` outranks the plan too.

**Rules for using this file**

1. Re-verify any row before writing code against it if the row is older than 30 days or the Claude Code version changed. Update the row and its date.
2. Never state an API field, flag, or contract clause you did not read in a source during the current session. Cite `file:line` or a fetched URL. If you cannot, write "unverified" and stop.
3. Verification levels: **[O]** read or measured by the orchestrator directly. **[V]** found by a reviewer and confirmed by an adversarial verifier with a `file:line` citation. **[R]** reported by one reviewer, not independently confirmed.

Sources: `https://code.claude.com/docs/en/hooks.md`, `.../plugins-reference.md`, `.../plugin-marketplaces.md`, `.../cli-reference.md`, `.../headless.md` (append `.md` to any docs path), `https://docs.typesafe.ai/llms.txt` (index; every page has a `.md` form), `https://openrouter.ai/docs/guides/community/typesafe-sdk.md`. Tested with Claude Code **2.1.278** on macOS arm64.

---

## A. Claude Code hooks

| # | Fact | Level |
|---|---|---|
| A1 | `PostToolUse` fires **only when a tool succeeds**. Failures fire the separate `PostToolUseFailure` event. | O |
| A2 | `PostToolUseFailure` input: `tool_name`, `tool_input`, `tool_use_id`, top-level `error` (string), `is_interrupt`, `duration_ms`. For Bash the `error` first line is `Exit code N`, then interleaved stdout and stderr, middle-truncated by Claude Code around `... [N characters truncated] ...`. A payload may have **no** exit-code line when the shell could not start. It does **not** fire for validation rejections or permission denials, so the absence of an error row is not proof of success. Output: top-level `decision:"block"` plus `reason`, which is returned to Claude as a tool error while the turn continues, or `hookSpecificOutput.additionalContext`. Exit 2 shows stderr to Claude without blocking. | O |
| A3 | `PostToolUse` input: `tool_name`, `tool_input`, `tool_response` (structured per tool; Bash is `{stdout, stderr, interrupted, isImage}`), `tool_use_id`, `duration_ms`. `duration_ms` excludes hook time, so hook-added latency must be self-timed. Output: top-level `decision:"block"` plus `reason`, or `hookSpecificOutput.{additionalContext, updatedToolOutput}`. | O |
| A4 | `PostToolUse` fires concurrently for parallel tool calls. `PostToolBatch` fires once per resolved batch with `tool_calls[]`. | O |
| A5 | `PreToolUse` output lives in `hookSpecificOutput`: `hookEventName:"PreToolUse"`, `permissionDecision` = `allow` \| `deny` \| `ask` \| `defer`, `permissionDecisionReason`, `updatedInput`, `additionalContext`. **`allow` skips the user's permission prompt**, except for actions no mode auto-approves and for `AskUserQuestion` and `ExitPlanMode`, which need a paired `updatedInput`. The user's own deny and ask permission rules are still evaluated whatever the hook returns. The reason is shown to Claude for `deny`, and to the user only for `allow` and `ask`. Top-level `decision` is deprecated for this event. | O |
| A6 | A **timed-out** command hook on `PreToolUse` does **not** block; the call proceeds through the normal permission flow. The docs say the `if` filter is best-effort and to "use the permission system rather than a hook to enforce a hard allow or deny". | O |
| A7 | `Stop` input: `stop_hook_active`, `last_assistant_message`, `background_tasks[]`, `session_crons[]`. Docs: use `last_assistant_message`, because the transcript "is written asynchronously and may lag" and "isn't guaranteed to include the final message at Stop time". Stop does not fire on user interrupt. API errors fire `StopFailure`, whose output is ignored. | O |
| A8 | `Stop` and `SubagentStop` output: top-level `decision:"block"` with a required `reason`, or `hookSpecificOutput.additionalContext` (non-error feedback that also continues the turn, labeled "Stop hook feedback"). Exit 2 plus stderr also blocks. The block `reason` stays in the conversation and Claude reads it. Claude Code force-ends the turn after **8 consecutive** stop-hook blocks. There is **no** `blockDecision` field. | O |
| A9 | `SubagentStop` adds `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message`. Since v2.1.271 a subagent using `SubagentHandback` delivers its report as that tool's `tool_input.message`, and `last_assistant_message` is only closing text. | O |
| A10 | Common input fields: `session_id`, `prompt_id` (UUID per user prompt, v2.1.196+, absent until first input), `transcript_path`, `cwd`, `scratchpad_dir`, `permission_mode` (`default`, `plan`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`), `effort.level`, `hook_event_name`. Inside subagents: `agent_id`, `agent_type`. `scratchpad_dir` and `effort` were not observed in any headless capture (see G14). | O |
| A11 | `UserPromptSubmit` input has `prompt` (the submitted text). `SessionStart` input has `source` = `startup` \| `resume` \| `clear` \| `compact` \| `fork`, and an optional `model`. | O |
| A12 | The transcript JSONL format is officially internal and "changes between versions, so scripts that parse these files directly can break on any release." | O |
| A13 | Handler fields: `type` (`command`, `http`, `mcp_tool`, `prompt`, `agent`), `matcher`, `if` (one permission-rule pattern, tool events only), `timeout` in seconds (defaults: **600** for `command`, `http`, `mcp_tool`; 30 for `prompt`; 60 for `agent`; the command default drops to 30 on `UserPromptSubmit` and to 10 on `MessageDisplay`; `SessionEnd` has its own budget, A17), `async` (command hooks only; cannot decide anything; result delivered next turn; **killed at teardown under `claude -p`**; `timeout` not enforced), `asyncRewake`, `statusMessage`. Exec form is `command` plus `args` with no shell. Shell form is a single string. All matching hooks run in parallel. | O |
| A14 | Matcher evaluation: `*`, empty, or omitted matches all. A value with only letters, digits, `_`, `-`, spaces, `,`, `\|` is an **exact-string list**. Any other character makes it an **unanchored JavaScript regex**, so anchor with `^...$`. There is no exclude keyword. | O |
| A15 | Claude Code reads stdout as JSON on **every** exit code, and only when the output starts with `{` and ends with `}`; anything else is plain text. Exit 2 is a blocking error whose reason is the JSON blocking decision when present and stderr otherwise. Anything else is a non-blocking error that prints a visible `hook error` notice in the transcript. A missing executable gives exit 127 and the same notice. Stderr on exit 0 goes only to the debug log, so it never reaches the user or Claude. | O |
| A16 | To show the **user** a non-blocking message at Stop, exit 0 with JSON `{"systemMessage": "..."}` and no `decision`. `suppressOutput` has no effect. `additionalContext`, `systemMessage` and plain stdout are capped at 10,000 characters each; over-cap text is spilled to a file Claude is not asked to read. | O |
| A17 | `SessionEnd` hooks share a 1.5-second budget, have no decision control, and their JSON output is discarded. The "Stop must finish in 1.5 s" idea in the original plan was this budget misattributed. Stop has the normal 600 s default. | V |
| A18 | Hooks fire normally under `claude -p`. `--bare` disables hooks and plugins. `--plugin-dir <path>` loads a local plugin. `--output-format stream-json --verbose --include-hook-events` streams `hook_started` and `hook_response` events, including for Stop, which lets an E2E test assert on a block decision. | V |
| A19 | Native `type:"prompt"` and `type:"agent"` hooks exist and are supported on Stop. `/goal` is a built-in shortcut for a session-scoped prompt-based Stop hook. This is the platform's own LLM-judge Stop hook and is a required baseline. | V |
| A20 | `PreToolUse` input for MCP tools carries a top-level `mcp_server` object (v2.1.274+). Docs say to base trust on `mcp_server.source`, not the tool-name prefix. Plugin-bundled MCP tools are named `mcp__plugin_<plugin>_<server>__<tool>`. | V |
| A21 | Built-in tool names listed by the hooks reference: `Bash`, `PowerShell`, `Edit`, `Write`, `Read`, `Glob`, `Grep`, `Agent`, `Workflow`, `WebFetch`, `WebSearch`, `AskUserQuestion`, `ExitPlanMode`. The tools reference also lists `NotebookEdit` and the task tools `TaskCreate`, `TaskUpdate` and others. **The subagent tool is `Agent`. There is no tool named `Task`.** Hooks match any tool except `EndConversation`. | O |

## B. Claude Code plugins

| # | Fact | Level |
|---|---|---|
| B1 | Layout: `.claude-plugin/plugin.json`, `hooks/hooks.json` (or inline), `skills/`, `agents/`, `bin/` (added to the Bash tool's PATH; not allowed in plugins distributed through claude.ai organization settings), `.mcp.json`. | O |
| B2 | Marketplace: `.claude-plugin/marketplace.json` in a repo. Install with `/plugin marketplace add owner/repo` then `/plugin install name@marketplace`, or `claude plugin ...` in a shell. CLI also has `plugin validate`, `plugin eval`, `plugin init`, `plugin tag`. | O |
| B3 | `userConfig` in `plugin.json` prompts at enable time. Types: `string`, `number`, `boolean`, `directory`, `file`. `sensitive: true` stores the value in the macOS Keychain, or `~/.claude/.credentials.json` elsewhere, with roughly 2 KB shared with OAuth tokens. String fields may have an `options` picker (v2.1.271+). | O |
| B4 | Every `userConfig` value is exported to hook processes as `CLAUDE_PLUGIN_OPTION_<KEY>`. Among hook commands, `${user_config.KEY}` substitutes only in exec form, where it lands in the process argv and is visible to `ps`; shell-form hooks that reference it fail outright. It also substitutes in MCP and LSP server configs. **Read secrets from the environment variable only.** | O |
| B5 | `pluginConfigs` values are read only from user settings, `--settings`, and managed settings. Project-level settings are ignored on purpose, so a cloned repo cannot inject values. | O |
| B6 | Hook process env: `CLAUDE_PLUGIN_ROOT` (install dir; **changes on update**; treat as ephemeral), `CLAUDE_PLUGIN_DATA` (`~/.claude/plugins/data/<id>/`; survives updates; **deleted on uninstall**), `CLAUDE_PROJECT_DIR`. | O |
| B7 | Marketplace-installed plugins are copied to a cache. Files outside the plugin directory are unavailable. | O |
| B8 | If `version` is unset in `plugin.json` on a git-hosted source, the version derives from the commit SHA and the plugin updates on every push. Set an explicit version. | V |
| B9 | Community marketplace submission is a form (`platform.claude.com/plugins/submit` for individuals), with validation and a nightly sync, not a GitHub pull request. Run `claude plugin validate <dir> --strict` first. | R |
| B10 | `claude plugin eval` (v2.1.269+) runs cases with and without the plugin and reports a score delta. Graders are fixed types such as `regex` and `tool_used`, so it cannot check exact hook JSON contracts. | V |

## C. TypeSafe Jev

| # | Fact | Level |
|---|---|---|
| C1 | Endpoint `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`. Request has exactly three fields, all required: `model`, `state` (string, object, or array), `questions` (map of name to question). No temperature or seed. | O |
| C2 | Question types: `noul` (`instructions`, optional `criteria` with `true` and `false` descriptions), `choice` (`criteria` map, up to 255 options), `score` (`criteria` ordered list, 2 to 10 levels). | V |
| C3 | Response: `{model, answers, usage:{input_tokens, output_tokens}}`. Noul answer is `{"type":"noul","noul":0.98}` and has **no confidence field**. Choice: `{choice, probabilities, confidence}`. Score: `{score, legend, probabilities, confidence}`. | O |
| C4 | Context: TypeSafe documents 64k tokens per request and 32k for `state` plus the longest question. OpenRouter lists the served model at **32,000**. Budget against 32,000, and keep state small anyway (see C9). | O |
| C5 | Price: $0.042 per million input tokens, output free (confirmed on OpenRouter's live listing). Rate limits: 250,000 tokens per second and 1,200 requests per minute, which TypeSafe says may change without notice. Over-limit returns 429. | O |
| C6 | Latency: docs say "most queries complete in about 100 ms". The "70 to 500 ms" figure in the original plan has no source. **No call has been made from this project yet.** Measure in M0. | O |
| C7 | Model ids: `jev-1.13.0` (direct), aliases `jev-latest` and `jev-preview`. Docs say to pin the versioned id if thresholds are tuned. Output is described as stable across repeats but not guaranteed deterministic. | V |
| C8 | Python SDK: `pip install typesafe-sdk` (Python 3.10+), `from typesafe_sdk import TypeSafeClient, Noul, Choice, Score`, `client.system_one(state, questions)`. Env: `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL`. Default timeout 10 s. The Python docs do not publish a retry-count default (the JavaScript SDK documents 2), so read the installed package before relying on one. The SDK redacts secret headers from logs but **not request bodies**. It is for eval and scripts only, never the hook hot path. | V |
| C9 | Documented weaknesses of jev-1.13: literal reading, **counting**, date comparison, **indirection and multi-hop**, **accuracy drops as state fills with irrelevant detail**, **state is not treated as hostile (prompt injection moves answers)**, contradictory instructions, no logical identities across separate questions (a noul and its negation scored 0.72 and 0.47), English-first. | O |
| C10 | Data: "Jev is not trained on customer requests or responses." Zero data retention is enterprise-only and arranged by contact. The default retention window is not published. | O |
| C11 | Keys: `https://console.typesafe.ai/keys`. Skill install: `claude plugin marketplace add typesafe-ai/skills` then `claude plugin install typesafe@typesafe-ai`. | V |
| C12 | The original plan's "about 68 percent agreement" figure, the "four published eval workflows" claim, and the `hello@typesafe.ai` showcase program have **no source** in TypeSafe's docs or site. Do not repeat them. | V |
| C13 | TypeSafe's Master Customer Agreement reportedly bars using Output to train or imitate a model. Anthropic's usage policy reportedly restricts training on Claude outputs. Both gate every learning extension. Re-read the primary texts before starting one. | R |

## D. OpenRouter access to Jev

| # | Fact | Level |
|---|---|---|
| D1 | OpenRouter serves Jev since 2026-09-18: `typesafe/jev-1.13` (canonical `typesafe/jev-1.13-20260917`) and alias `~typesafe/jev-latest`. Same price as direct. Modality `text->decisions`. | O |
| D2 | These models are **hidden** from the default `GET /api/v1/models`, which filters to text output. Use `?output_modalities=all`. | O |
| D3 | Wire-compatible endpoint: `POST https://openrouter.ai/api/v1/systemone`, same request and response shapes, plus `id`, `provider`, and `usage.cost`. Bare ids `jev-1.13` and `jev-latest` are mapped automatically. With the SDK, set `base_url="https://openrouter.ai/api"`. | O |
| D4 | A separate `/api/alpha/decisions` surface exists and is labeled Alpha. Do not use it. | R |
| D5 | Requesty, LiteLLM, and Netlify AI Gateway are **not** verified access paths. They are removed from the plan. | O |

## E. Measured on the development machine (macOS arm64)

| # | Measurement | Level |
|---|---|---|
| E1 | Cold process start, median of 10 (Python 3.13): bare interpreter 15 ms; stdlib `json+sqlite3+http.client+ssl` 24 ms; pydantic 32 ms; yaml 27 ms; httpx 50 ms; sqlalchemy 97 ms; `sqlalchemy.orm` 123 ms; pydantic + sqlalchemy.orm + httpx + yaml **154 ms**. Under `/usr/bin/python3` 3.9.6 the same stdlib imports cost about 34 ms plain and with `-S`, but **about 101 ms with `-E`** (median of 12; `-E` alone causes the whole regression). Homebrew 3.14 is about 29 ms with any flag. `-S` alone still blocks site-packages and keeps the script directory at `sys.path[0]`. | O |
| E2 | Cold connection per process, no keep-alive. `api.typesafe.ai`: TCP about 110 ms, TLS done about 150 ms, first byte of an unauthenticated rejection about 260 ms. `openrouter.ai`: TCP 9 ms, TLS 22 ms, first byte 43 ms. OpenRouter is likely the faster route for one-shot hook processes. Confirm with a key in M0. | O |
| E3 | **TLS trap:** the python.org framework Python 3.14 that is first on PATH fails `CERTIFICATE_VERIFY_FAILED` against both providers, because its OpenSSL CA file does not exist. `/usr/bin/python3` 3.9.6, Homebrew 3.14, and uv 3.13 all succeed. Loading `/etc/ssl/cert.pem` when the default store is empty fixes it with verification still on. | O |
| E4 | No `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, or `ANTHROPIC_API_KEY` was set as of 2026-09-20. | O |
| E5 | With the D-012 fallback (load `/etc/ssl/cert.pem` when the default store is empty), the TLS-broken framework Python 3.14 connects to `openrouter.ai` in about 34 ms with verification on. | O |

## F. Prior art

| # | Fact | Level |
|---|---|---|
| F1 | `valentynkit/jev-belay` (JavaScript, created 2026-09-18): a Stop hook using Jev for the "unverified done" problem. It reads the transcript, uses two regex evidence gates so only 17.7 percent of 2,694 stops reach Jev, asks four questions in one call (median 1,222 input tokens, $0.00005, 346 ms median, 433 ms p90), and reports AUROC 0.976 on 100 labeled stops against 0.777 from wording alone. It has shadow mode and a Keychain-stored key. Its stated blind spot is turns with no file edits. It notes that Claude Code "records a runner's nonzero exit with no error flag anywhere" in the transcript. | O |
| F2 | `Dicklesworthstone/destructive_command_guard` (Rust, about 6,000 stars): rule-based `PreToolUse` blocker for destructive git and shell commands. No model, sub-millisecond. | O |
| F3 | `noplan-inc/limpet`: measured a "do not say done without running tests" rule at AUROC 0.50. Its README reportedly says 2,645 stops with a noisy behavioral label. Quote it from the source, never through jev-belay. | R |
| F4 | About 60 Jev-related repositories were created around 2026-09-21, including Jev review dashboards and several awesome-jev lists. Do not use "first" or "only" framing. | R |
| F5 | PyPI `verdict` belongs to Haize Labs, an LLM-judge library with no console script. `agent-verdict`, `tracejudge`, and `exitproof` are free on PyPI, npm, and as GitHub user names. Domain availability is unverified. | O |

## G. Observed in captured fixtures (Claude Code 2.1.278, captured 2026-09-21)

Source: real payloads in `tests/fixtures/hooks/*.json` and `tests/fixtures/hooks/PROVENANCE.md`, captured by `make capture-fixtures` and recorded in `.superpowers/sdd/2026-09-20-m0-foundations/task-4-report.md` (Step 10, and Fix round 1 item 2).

| # | Fact | Level |
|---|---|---|
| G1 | `SessionStart` top-level keys: `cwd, hook_event_name, session_id, source, transcript_path`. | O |
| G2 | `UserPromptSubmit` top-level keys: `cwd, hook_event_name, permission_mode, prompt, prompt_id, session_id, transcript_path`. | O |
| G3 | `PreToolUse` top-level keys (same across Bash, Write, Edit, Read, and Agent fixtures): `cwd, hook_event_name, permission_mode, prompt_id, session_id, tool_input, tool_name, tool_use_id, transcript_path`. | O |
| G4 | `PostToolUse` top-level keys (same across Bash, Write, Edit, Read, and Agent fixtures): `cwd, duration_ms, hook_event_name, permission_mode, prompt_id, session_id, tool_input, tool_name, tool_response, tool_use_id, transcript_path`. | O |
| G5 | `PostToolUseFailure` top-level keys (Bash fixture): `cwd, duration_ms, error, hook_event_name, is_interrupt, permission_mode, prompt_id, session_id, tool_input, tool_name, tool_use_id, transcript_path`. No `tool_response`, consistent with A2. | O |
| G6 | `Stop` top-level keys: `background_tasks, cwd, hook_event_name, last_assistant_message, permission_mode, prompt_id, session_crons, session_id, stop_hook_active, transcript_path`. | O |
| G7 | `SubagentStop` top-level keys: `agent_id, agent_transcript_path, agent_type, background_tasks, cwd, hook_event_name, last_assistant_message, permission_mode, prompt_id, session_crons, session_id, stop_hook_active, transcript_path`. | O |
| G8 | `SessionEnd` top-level keys: `cwd, hook_event_name, prompt_id, reason, session_id, transcript_path`. | O |
| G9 | The subagent tool's `tool_name` is `Agent` (`pre_tool_use_agent.json`, `post_tool_use_agent.json`), confirming A21. Observed `agent_type`: `general-purpose` (`subagent_stop.json`, and `tool_input.subagent_type` in `pre_tool_use_agent.json`). | O |
| G10 | Keys of Bash `tool_response` on `PostToolUse` (`post_tool_use_bash.json`): `interrupted, isImage, noOutputExpected, stderr, stdout`. `noOutputExpected` is not documented in A3, which lists only `{stdout, stderr, interrupted, isImage}`. | O |
| G11 | Exact first line of the `error` string on the Bash `PostToolUseFailure` fixture (`post_tool_use_failure_bash.json`): `Exit code 3`. | O |
| G12 | `SessionEnd` carries a `reason` field. Observed value in the captured fixture (`session_end.json`): `other`. | O |
| G13 | `prompt_id` is absent on `SessionStart` (`session_start.json` has no `prompt_id` key), consistent with A10 ("absent until first input"). It is present on all other seven captured event types (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `SubagentStop`, `SessionEnd`). | O |
| G14 | A10's documented common fields `scratchpad_dir` and `effort` were **not observed** in any of the 16 captured fixtures, across all eight event types. Parsers must treat both as optional. | O |
| G15 | **Orphan pre rows.** When a `PreToolUse` hook denies a call (observed: the owner's global "Fact-Forcing Gate" `PreToolUse` hook denying the first Bash or Write call of each headless session, per A2's "does not fire for validation rejections or permission denials"), Claude Code fires `PreToolUse` but neither `PostToolUse` nor `PostToolUseFailure` for that `tool_use_id`, and the model's retry arrives with a new `tool_use_id`. Consequence for the ledger: a `pre` row with no matching `post` row means denied or unknown, never a failure. | O |
