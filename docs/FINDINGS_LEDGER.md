# Findings Ledger

One row per finding from the plan review of 2026-09-20 (seven reviewers, four adversarial verifiers, one completeness critic).
Full finding text, evidence and verifier notes: `docs/research/plan-review-2026-09-20.json`.
Disposition is the orchestrator's decision. `docs/PLAN.md` already reflects every Applied row.

Verifier column: confirmed, modified (verifier corrected a detail and the corrected version was used), or n/a (lens had no verifier).

| ID | Lens | Severity | Verifier | Finding | Disposition | Note |
|---|---|---|---|---|---|---|
| PRIOR-1 | prior-art | critical | n/a | PreToolUse 'dangerous' gate duplicates a mature, free, offline competitor (destructive_command_guard) | Applied (modified) | v1 PreToolUse gate is deterministic rules only. Jev gate is an M6 experiment that must beat a rule-pack baseline. README recommends dcg alongside. |
| PRIOR-2 | prior-art | critical | n/a | Stop/SubagentStop completion verifier (4.3) is a near-duplicate of jev-belay, which already ships the identical mechanism with a published ablation | Applied (modified) | Evidence gating adopted and jev-belay credited. Differentiator is the hook-recorded exit-code ledger, not per-call Jev verdicts, because failures are deterministic events. |
| PRIOR-3 | prior-art | high | n/a | The Jev/TypeSafe ecosystem is not a quiet greenfield — dozens of competing tools appeared in the same ~90-minute window | Applied |  |
| PRIOR-4 | prior-art | high | n/a | TypeSafe's own documented model limitations directly undermine two load-bearing design assumptions in Section 4 | Applied |  |
| PRIOR-5 | prior-art | high | n/a | Context budget in Section 2.1 misstates Jev's actual limits, and Section 4.3's compression target is derived from the wrong number | Rejected | Superseded by COMP-6: budget against the smallest served context (32,000 via OpenRouter). Both TypeSafe limits are recorded in VERIFIED_FACTS. |
| PRIOR-6 | prior-art | high | n/a | VERIFIED_FACTS.md's stated size of limpet's calibration run (1,500 stops) does not match limpet's own README (2,645 stops), and the two AUROC-0.50 findings use incompatible label definitions | Applied |  |
| PRIOR-7 | prior-art | medium | n/a | Section 2.1's '~68% agreement with frontier-LLM reference answers' figure has no match anywhere in TypeSafe's docs | Applied |  |
| PRIOR-8 | prior-art | medium | n/a | Section 12's TypeSafe outreach plan is built on an unverified claim about a showcase program and contact email | Applied |  |
| PRIOR-9 | prior-art | medium | n/a | PreToolUse gate has no evidence-gating equivalent, so it will pay Jev's latency/cost on nearly every tool call | Applied |  |
| PRIOR-10 | prior-art | medium | n/a | Phase 0's TypeSafe skill marketplace command is unverified and could block day 1 | Rejected | The skill install command was confirmed in docs agent-skill.md by the TypeSafe fact-sheet worker. M0 still treats it as non-blocking. |
| PRIOR-11 | prior-art | low | n/a | The 'Verdict' name collides with more than the already-flagged PyPI package | Applied (modified) | One identifier everywhere: agent-verdict. Final name is gate G0.1 and an open decision for the owner. |
| PRIOR-12 | prior-art | medium | n/a | The plan lacks a stated differentiation thesis against this prior art; the sharpest honest positioning is a lifecycle/replay claim, not a better Stop hook | Applied (modified) | Positioning rewritten around exit-code ledger, calibration analysis, and a reproducible benchmark. |
| LEGAL-1 | legal | critical | n/a | TypeSafe's Master Customer Agreement bars using Jev's Output to train another model — directly threatens Section 15.2 | Deferred (gated) | Section 15 learning extensions are blocked until the owner re-verifies TypeSafe terms and gets written clarification. Not independently verified by the orchestrator. |
| LEGAL-2 | legal | critical | n/a | Anthropic's Usage Policy separately bars training a model on Claude Code inputs/outputs without authorization — same Section 15.2 risk from the other side | Deferred (gated) | Same gate as LEGAL-1 for Anthropic usage policy. Applies to any learned artifact, including bandits (COMP-12). |
| LEGAL-3 | legal | high | n/a | Plan cites TypeSafe facts (68% agreement figure, 'showcase'/outreach program) that do not exist anywhere in the 895KB TypeSafe docs dump | Applied |  |
| LEGAL-4 | legal | high | n/a | OpenRouter's Jev access point used in some SDK docs is the explicitly-labeled 'Alpha' Decisions endpoint, not the stable systemone proxy the plan should use | Applied |  |
| LEGAL-5 | legal | high | n/a | TypeSafe's Zero Data Retention is enterprise-only and manually arranged; the DPA sets no fixed retention window on the standard API tier | Applied |  |
| LEGAL-6 | legal | medium | n/a | TypeSafe's 'no training on your data' privacy commitment does not automatically transfer to the OpenRouter fallback path the plan treats as interchangeable | Applied |  |
| LEGAL-7 | legal | medium | n/a | Section 12's plugin-marketplace submission mechanism doesn't match Anthropic's actual process | Applied |  |
| LEGAL-8 | legal | medium | n/a | Anthropic's guardrail-bypass AUP clause, read in full, does not prohibit Verdict's Stop-blocking mechanism — worth recording explicitly | Applied |  |
| LEGAL-9 | legal | medium | n/a | TypeSafe's liability cap and no-retention-on-termination terms aren't in the Section 14 risk table | Applied |  |
| LEGAL-10 | legal | low | n/a | TypeSafe's Terms-of-Use confidentiality clause is scoped to the marketing site, not the API — don't cite it as an API restriction | Applied |  |
| LEGAL-11 | legal | medium | n/a | TypeSafe explicitly warns its published rate limits are provisional and can drop without notice — not reflected as its own risk line | Applied |  |
| LEGAL-12 | legal | medium | n/a | Telemetry design describes hashed install id + collector-visible IP as 'anonymous,' which GDPR/CCPA treat as personal data | Applied (modified) | Wording fixed now (pseudonymous, never anonymous). Telemetry itself is M6. |
| CC-1 | cc-docs | high | confirmed | Plan never benchmarks against Claude Code's own native LLM-judge Stop hook (prompt/agent hook types, /goal) | Applied |  |
| CC-2 | cc-docs | high | confirmed | Section 4.3's 'flag' mechanism (print via stderr) is invisible to the user on an allow-stop; must use JSON systemMessage instead | Applied |  |
| CC-3 | cc-docs | medium | confirmed | Section 2.2's 'Stop ≤ 1.5s' budget conflates the SessionEnd hook's hard 1.5s timeout with the Stop hook, which actually defaults to 600s | Applied |  |
| CC-4 | cc-docs | medium | modified | Shell-form Python hook commands can fail silently and cross-platform, disabling the safety gate with no visible error | Applied (modified) | Exec-form launcher script inside the plugin, interpreter probe, and a system CA bundle fallback verified on this machine. |
| CC-5 | cc-docs | medium | modified | No built-in matcher syntax excludes read-only tools; Section 4.1/4.5's 'skip Jev for read-only tools' requires an explicit hand-written regex | Applied (modified) | Explicit allowlist regex matcher instead of negative lookahead. PostToolUse success rows for read-only tools are intentionally not recorded (D-008). |
| CC-6 | cc-docs | medium | modified | Section 7's hook E2E row doesn't name the flag that actually exposes hook decisions in headless output | Applied |  |
| CC-7 | cc-docs | medium | confirmed | `claude plugin eval` is a ready-made, CI-gateable WITH/WITHOUT-plugin harness the plan doesn't use, but it can't replace the stdin/stdout contract tests | Applied |  |
| CC-8 | cc-docs | medium | confirmed | Undefined plugin version strategy risks either update-per-commit or update-never, contradicting Section 14's own risk row | Applied |  |
| CC-9 | cc-docs | medium | modified | MCP-tool trust/classification in Section 4.1 should key on `mcp_server.source`, not tool-name prefix, and must account for plugin-scoped MCP tool names | Applied (modified) | mcp_server fields are recorded in the ledger now. Trust classification is part of the M6 Jev gate experiment. |
| CC-10 | cc-docs | low | confirmed | Section 8.2's on-device hook latency metric can't be derived from PostToolUse's tool_response.duration_ms | Applied |  |
| CC-11 | cc-docs | low | modified | Section 3.2's sessions table (started_at/ended_at/outcome) has no hook wired to populate it, and SessionEnd cannot make decisions | Applied |  |
| EVAL-1 | eval | critical | modified | n=300 with 60 positives cannot support any of the published comparisons; CI half-widths are ±8–10 points | Applied (modified) | Implemented as publication tiers in code (COMP-4). |
| EVAL-2 | eval | critical | modified | ECE with 10 equal-width bins at n=300 is indistinguishable from noise; a perfectly calibrated model scores 0.040 | Applied |  |
| EVAL-3 | eval | critical | confirmed | "Jev is stochastic; report mean ± std over 3 runs" contradicts the TypeSafe docs and reports the wrong variance component | Applied |  |
| EVAL-4 | eval | high | modified | Noul answers have no `confidence` field, so the confidence-vs-accuracy analysis is undefined for 5 of the 8 shipped questions and `verdicts.confidence` is always NULL | Applied |  |
| EVAL-5 | eval | high | confirmed | Tuning on synthetic and reporting on real is not enough: no real tuning split, no pre-registration, no defined hash | Applied |  |
| EVAL-6 | eval | high | modified | The synthetic generator leaks its own labels into the state and produces trivially separable positives | Applied |  |
| EVAL-7 | eval | high | confirmed | The blog headline metric has no defined denominator and, from 4–6 people, has a design effect near 6 | Applied |  |
| EVAL-8 | eval | high | confirmed | Label mode shows Jev's probabilities next to the label button, and 20% double-labeling of 300 gives a ±0.17 CI on kappa | Applied (modified) | Blinded labeling, 50 percent double labeling and CI-based kappa gate adopted. Session swapping rejected: raw text never leaves the contributor machine (SEC-10). Self-labeling bias is disclosed. |
| EVAL-9 | eval | high | modified | "Recovery rate" and "the additionalContext nudge changes behavior" are causal claims with no control arm | Applied |  |
| EVAL-10 | eval | high | modified | The LLM-judge baselines are not specified tightly enough to be fair, and the "40–100× cheaper and faster" claim depends entirely on unstated pricing mode | Applied |  |
| EVAL-11 | eval | medium | confirmed | The CI guard "fail if precision drops >5 points" fires on pure noise 11–17% of the time | Applied |  |
| EVAL-12 | eval | medium | modified | Requesting `jev-latest` makes every published number unreproducible, and the alias can move under you mid-eval | Applied |  |
| ARCH-1 | architecture | critical | modified | Event model is wrong at the root: PostToolUse never sees a failure, and read-only tools must be excluded by matcher, not by policy | Applied (modified) | Event model adopted. All recorders are synchronous and network-free in v1; no async hooks (D-004). |
| ARCH-2 | architecture | critical | confirmed | The hot path as specified cannot meet the plan's own 150 ms startup rule: choose option C, a stdlib-only vendored hot path with a pinned interpreter | Applied (modified) | Stdlib-only vendored hot path adopted. Interpreter is probed, not pinned to /usr/bin/python3 (COMP-9). |
| LAT-1 | architecture | critical | modified | PreToolUse latency budget has no headroom, the plan's two timeouts contradict each other, and the hooks.json timeout defaults to 600 s | Applied (modified) | No Jev call on PreToolUse in v1, so the tight budget applies only to the M6 experiment. Stop deadlines are provider-derived and measured in M0. |
| ARCH-3 | architecture | high | confirmed | The tool ledger must be built from Verdict's own hook events, not from the transcript; the transcript parser belongs behind a versioned adapter used only offline | Applied |  |
| REL-1 | architecture | high | confirmed | Async PostToolUse recorder races the Stop check: make the failure recorder synchronous, bound the Stop wait, and define unknown as benign | Applied |  |
| REL-2 | architecture | high | confirmed | Loop guard must be keyed on prompt_id, and Stop must stand down for background tasks, plan mode, and subagents | Applied (modified) | Per-prompt loop guard and stand-down rules adopted, except Stop verification stays active under bypassPermissions, where unattended runs need it most. |
| DB-1 | architecture | high | confirmed | SQLite under parallel hook processes: pin the pragmas, keep the DB out of CLAUDE_PLUGIN_DATA, and make retention a shipped command | Applied (modified) | Moot for the hot path, which writes append-only JSONL (SEQ-2, COMP-2). Pragmas apply to the derived SQLite index. Payload caps, retention and prune adopted. |
| PKG-1 | architecture | high | modified | The repo layout cannot be installed as a plugin: the manifest location, the copied-plugin boundary, and the console-script assumption all break | Applied |  |
| JEV-1 | architecture | high | confirmed | Calling Jev on every PostToolUse is the wrong spend: gate it on soft-failure evidence, and skip it entirely for calls whose exit code already answers the question | Applied |  |
| CFG-1 | architecture | high | confirmed | Policy format, config precedence and the PreToolUse cache: YAML cannot be read by the hot path, so compile it to JSON | Applied (modified) | Policy is JSON with a JSON Schema. No YAML in v1, because plugin-only installs have no compiler. |
| PROV-1 | architecture | medium | confirmed | Collapse the provider abstraction to one HTTP client with two configs and drop the three unverified fallbacks | Applied |  |
| LAT-2 | architecture | medium | modified | No local keep-alive daemon in v1: the measured connection setup does not justify the failure modes it adds | Applied |  |
| PHASE-1 | architecture | medium | confirmed | Phase 1-3 ordering and the Section 3.1 component table bake in the pre-rewrite event model; reorder so the recorder and the fixtures come first | Applied |  |
| SEC-1 | security | critical | confirmed | Section 4.1's default "Else allow" makes Verdict silently widen the user's permission prompts | Applied |  |
| SEC-2 | security | critical | confirmed | The gate is sold as a blocker but is documented best-effort; the deterministic layer must fail closed while only the model layer fails open | Applied |  |
| SEC-3 | security | high | modified | Ledger status must come from PostToolUseFailure exit codes, not from a model probability; Section 4.2 is built on an event that never fires on failure | Applied |  |
| SEC-4 | security | high | modified | Prompt injection into the verifier, and injection amplification through the Stop block reason | Applied |  |
| SEC-5 | security | high | confirmed | Data egress is one sentence in the plan: no never-send list, no local-only mode, no measured catch rate, and retention at the provider is not zero by default | Applied |  |
| SEC-6 | security | high | modified | Redaction should be a compiled port of the gitleaks rule set with a measured catch rate, not hand-rolled regexes | Applied |  |
| SEC-7 | security | high | modified | API key: keep it out of shell history, out of hook.log, and out of SDK request-body logging | Applied |  |
| SEC-8 | security | high | modified | The hosted playground is an unauthenticated Jev proxy on the author's key that collects other people's transcripts | Deferred to M6 | Playground is M6. All constraints are recorded in PLAN section 12. |
| SEC-9 | security | high | confirmed | Local dashboard: stored XSS from tool output, plus no binding, token, CSRF or Host check on the label endpoints | Applied (modified) | Output escaping applies to the static HTML report now. Server hardening applies when the web dashboard ships in M6. |
| SEC-10 | security | high | confirmed | Gold-set collection from friends' sessions needs a written consent and scrubbing process, and only derived features should ever be committed | Applied |  |
| SEC-11 | security | medium | confirmed | Local store holds raw tool inputs and outputs in plaintext with no permissions, retention window, or purge command | Applied |  |
| SEC-12 | security | medium | modified | Telemetry is pseudonymous, not anonymous, and the "N installs" headline rides on an unauthenticated ingest endpoint | Deferred to M6 | Telemetry is M6. Wording and design constraints recorded now. |
| SEC-13 | security | medium | confirmed | Supply chain: plugin updates execute on users' machines every session, and the documented bootstrap pattern installs dependencies from the network with no integrity check | Applied |  |
| SCOPE-1 | scope | critical | n/a | The 35-day plan is ~2.5-3x over a part-time budget and produces nothing publishable before day 21 | Applied (modified) | Milestones M0 to M6 with hour estimates replace the 35-day phases. |
| DIFF-1 | scope | critical | n/a | The smallest differentiated v0.1 is a PostToolUseFailure-derived failure ledger, and the plan never mentions the event that makes it differentiated | Applied |  |
| DATA-1 | scope | critical | n/a | The gold-set target (300 hand labels, ≥60 positives per question) is arithmetically unreachable in 35 days and `dangerous` is unreachable at any scale from real sessions | Applied |  |
| SEQ-1 | scope | critical | n/a | Data collection and friend recruitment are scheduled after the work that depends on them | Applied (modified) | Collector ships first. Contributors share derived rows and labels only, never raw JSONL (SEC-10). |
| SCOPE-2 | scope | high | n/a | Cut every hosted component from v1: they consume ~40% of the budget and produce no number anyone will read | Applied (modified) | Hosted API, site, playground, telemetry, Docker and the web dashboard move to post-launch M6 instead of being deleted. Cutting them entirely is the owner's call. |
| METRIC-1 | scope | high | n/a | The install targets cannot be measured: telemetry is default-off and no marketplace install counter is documented | Applied |  |
| NAME-1 | scope | high | n/a | Rename: the Verdict mark is blocked on PyPI, npm and GitHub, and the PyPI holder is an LLM-judge library you plan to benchmark against | Applied (modified) | Single identifier adopted (agent-verdict). Rename to exitproof or tracejudge remains an open decision at gate G0.1. |
| LAUNCH-1 | scope | high | n/a | Make jev-belay a credited baseline arm rather than something to route around; its two published blind spots are your headline experiment | Applied |  |
| ACCEPT-1 | scope | medium | n/a | Most phase acceptance criteria are unfalsifiable as written; restate each as a command with pass/fail output | Applied |  |
| RISK-1 | scope | medium | n/a | The published report is pinned to a moving alias on a two-day-old third-party endpoint | Applied |  |
| SEQ-2 | scope | medium | n/a | The Phase 2 store violates the plan's own hook-startup budget, and the Section 16 kickoff prompts bundle too much per session to be steerable | Applied |  |
| RISK-2 | scope | low | n/a | Marketplace listing is treated as a launch milestone and the LLM-judge baseline cost is unbudgeted | Applied |  |
| COMP-1 | completeness | critical | n/a | CLAUDE.md is specified as generate-from-this-doc, i.e. from the document seven reviewers just falsified | Applied |  |
| COMP-2 | completeness | critical | n/a | No mechanism records which reviewer won: five pairs of findings are mutually exclusive | Applied |  |
| COMP-3 | completeness | critical | n/a | Whether Section 4.1 ships in v1 is unresolved across four findings, and the product's one-sentence pitch depends on it | Applied (modified) | Rules-only gate in v1. The Jev gate stays on the roadmap as an M6 experiment rather than being deleted. |
| COMP-4 | completeness | critical | n/a | Gold-set scale is specified three incompatible ways and no reviewer set the publication gate that reconciles them | Applied |  |
| COMP-5 | completeness | high | n/a | Every latency and cost number is secondhand or modeled: no API key exists and no phase gate forces a measurement | Applied |  |
| COMP-6 | completeness | high | n/a | Context budget is provider-dependent and the authoritative fact contradicts the reviewer who corrected it | Applied |  |
| COMP-7 | completeness | high | n/a | Verdict has no observability of itself: hook.log is named once and never specified, and there is no doctor command | Applied |  |
| COMP-8 | completeness | high | n/a | Uninstall, upgrade and a kill switch are absent from the plan and from all seven reviews | Applied (modified) | Kill switch and schema_v adopted. There is no settings merge in v1 (plugin install is the only path), so uninstall is plugin uninstall plus verdict purge. |
| COMP-9 | completeness | high | n/a | No supported-platform statement, yet every measurement and several design choices are macOS-arm64-only | Applied |  |
| COMP-10 | completeness | medium | n/a | The developer loop is undefined: no Makefile targets, no fixture-capture command, no way to run one hook by hand, no cheap real-session test | Applied |  |
| COMP-11 | completeness | medium | n/a | Cost ceilings exist only for the judge baseline, and the token tax Verdict imposes on the user's own context is never counted | Applied |  |
| COMP-12 | completeness | medium | n/a | Section 15 is load-bearing for the plan's own replay design yet only 15.2 was reviewed, and verdict replay has no acceptance criterion | Applied |  |

## Final document review (2026-09-20)

After the documents were written, three more agents reviewed them: a fact-checker against the saved primary sources, a consistency reviewer, and a fresh agent dry-running M0 and M1. All issues were applied to the documents the same day. Full text: `docs/research/final-doc-review-2026-09-20.json`.

| ID | Reviewer | Severity | Issue | Disposition |
|---|---|---|---|---|
| VF-01 | factcheck | critical | The `/usr/bin/python3` 3.9.6 cold-start figure was measured without the `-S -E` flags the launcher in PLAN 4.2 mandates | Applied |
| VF-02 | factcheck | high | The stated output contract for `PostToolUseFailure` is wrong: the event also honors top-level `decision: "block"` with a `reason`, which is delivered  | Applied |
| VF-03 | factcheck | high | `Task` is not a Claude Code tool name, so that alternative in the PostToolUse matcher can never fire | Applied |
| VF-04 | factcheck | high | Same non-existent tool name in the G-SOFT evidence gate | Applied |
| VF-05 | factcheck | medium | Two errors in the exit-code contract | Applied |
| VF-06 | factcheck | medium | The launcher's interpreter fallback chain ends at `python3` on PATH, which on this machine is the python.org framework 3.14 that E3 documents as unabl | Applied (modified): the D-012 CA fallback makes the PATH interpreter work (E5); `verdict doctor --fix-interpreter` validates candidates. |
| VF-07 | factcheck | medium | "default 600 for command hooks" is presented as unconditional but is only the default on most events | Applied |
| VF-08 | factcheck | medium | The bolded claim that `allow` skips the user's permission prompt drops two documented exceptions, both security-relevant for a plugin that ships a Pre | Applied |
| VF-09 | factcheck | low | `compact` is a valid SessionStart source and is the only one the matcher omits, with no stated reason | Applied |
| VF-10 | factcheck | low | "`${user_config.KEY}` substitution happens only in exec-form hook commands" is too narrow | Applied |
| VF-11 | factcheck | low | Level [V] is overstated for the "2 retries" default | Applied |
| VF-12 | factcheck | low | The isolation claim is correct as far as it goes but incomplete in a way that matters for the section 8 import ban: `-S -E` removes site-packages and  | Applied |
| V-01 | consistency | critical | The 14-day retention default destroys the study corpus before it can be labeled | Applied |
| V-02 | consistency | high | The randomized arm experiment is listed as an M4 deliverable but can produce no arm-1 data at M4, and it contradicts D-014 | Applied: the randomized arm and the nudge moved to M6; `arm` is no longer an M1 field. |
| V-03 | consistency | high | M1 recorders must write ledger fields whose producers are not built until M2 | Applied |
| V-04 | consistency | high | Contributors are recruited and install the collector on day 4 (M1), but the redactor that writes their ledger is explicitly incomplete until M2 and it | Applied |
| V-05 | consistency | high | PLAN gives the SessionStart hook a network call, which D-004 forbids; DECISIONS outranks PLAN | Applied |
| V-06 | consistency | high | The headline number cannot be published under the plan's own default contributor plan | Applied |
| V-07 | consistency | high | G3.4 is not a testable gate: `verdict stats` reads only the local `~/.verdict`, so it cannot show stops "across all machines", and the gate itself adm | Applied |
| V-08 | consistency | high | G2.6 invokes `verdict bench`, a command no milestone builds and which appears in no command list | Applied |
| V-09 | consistency | high | G1.6 requires `verdict doctor` to exit 0 at M1 including under the TLS-broken interpreter, but the code that makes that possible ships in M2 | Applied |
| V-10 | consistency | medium | The architecture makes six commands read the derived SQLite index, but `verdict index` is an M3 deliverable while `stats` and `doctor` ship in M1 and  | Applied |
| V-11 | consistency | medium | This is impossible as specified | Applied |
| V-12 | consistency | medium | The heading tells a coding agent that every listed command is available from M0, when most are built much later: `make index / replay / report / docto | Applied |
| V-13 | consistency | medium | G1.1 runs `make check`, which includes a ledger-schema test, but `schemas/ledger-v1.json` appears only in the repo-layout tree and is not a deliverabl | Applied |
| V-14 | consistency | medium | The plan asserts two stated blind spots for jev-belay, but the verified fact records one | Applied |
| V-15 | consistency | medium | Scope from the original plan disappeared with no disposition: the `outcomes` record of whether the task actually succeeded | Applied |
| I1 | dryrun | critical | Section 4.2's SessionStart hook spec requires a network reachability probe on every session, but section 1 defines the v0.1 collector milestone (which | Applied |
| I2 | dryrun | critical | VERDICT_HOME is used throughout the dev loop and gate commands (e.g | Applied |
| I3 | dryrun | high | M1's CLI spec requires `verdict stats` to report 'stops with claims', but claim extraction (`claims.py`) is explicitly scheduled for M2 (section 4.1,  | Applied |
| I4 | dryrun | high | The CLI component is required to reuse `plugin/hooks/verdict_hot/` 'through a build step that copies [it] into the wheel, with a CI check that the tre | Applied |
| I5 | dryrun | high | CLAUDE.md states the plugin registers all eight events (including PreToolUse and SubagentStop) as the architecture, but M1's task list only builds rec | Applied |
| I6 | dryrun | high | Section 4.3's ledger schema unconditionally lists `claims[]`, `gate_reason`, `background_tasks_n`, and `arm` as fields on every `stop` row under `sche | Applied |
| I7 | dryrun | medium | Section 4.3 lists `prompt_id` as a common field on every ledger row without qualification, but VERIFIED_FACTS.md states `prompt_id` is 'absent until f | Applied |
| I8 | dryrun | medium | The G-STOP evidence gate and the loop guard (D-015) are both keyed by `prompt_id`, but neither specifies behavior when the current Stop event's `promp | Applied |
| I9 | dryrun | medium | The launcher resolution order names `~/.verdict/interpreter` as a fallback source of the Python interpreter path, but no section specifies who creates | Applied |
| I10 | dryrun | medium | M1's task list requires sending `docs/CONSENT.md` to contributors on day 4, and section 9.1 specifies what that document must contain, but no mileston | Applied |
| I11 | dryrun | medium | G0.1 requires `scripts/check_name.sh` to exit 0 as an M0 gate, and the script is described as checking 'PyPI, npm, GitHub user, existing Claude Code p | Applied (modified): registry checks specified; the reserved-marketplace-name check stays manual until a source is verified. |
| I12 | dryrun | medium | M0's `make capture-fixtures` step requires capturing real hook stdin for all eight events using 'a record-only stub plugin,' but the real launcher, `h | Applied |
| I13 | dryrun | low | The M0 smoke-test command passes `--provider` twice in a single invocation, which is unusual CLI syntax that is never explained | Applied |
| I14 | dryrun | low | CLAUDE.md describes `make check` as running '.. | Applied (modified): the findings-ledger test is defined against the research JSON, not code comments. |
| I15 | dryrun | low | M1's `verdict doctor` spec includes a per-provider TLS handshake check and 'key present or absent' reporting, and G1.6 requires `doctor` to exit 0 on  | Applied |
