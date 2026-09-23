# Demo site design: agent-verdict on Vercel

Date: 2026-09-23. Status: approved in conversation by the owner; this document is the spec the implementation plan argues from.

## Purpose

A single public page that lets a visitor watch agent-verdict catch an agent claiming success its own tool results do not support, using real recorded sessions. It exists to demo the project in about sixty seconds and to give the README a place to point. It is not documentation (the repo's `docs/` remain the source) and it is not a live judge (no visitor ever triggers a provider call).

Success looks like: a visitor with no context understands what the tool catches within the hero animation, can step through a real session and see the numbers behind the block, and can copy one install command. Every number on the page traces to a command in this repo.

## Non-goals

- No live provider calls from the site, no API key anywhere near Vercel.
- No docs section, blog, or multi-page navigation.
- No analytics, cookies, third-party scripts, or fonts loaded from outside the allowed CDNs.
- No hand-written scenario data. If it is on the page, a script produced it from a real ledger.

## Where it lives

`site/` in the agent-verdict repo. Vercel project root is `site/`, static output. The plugin's hot-path rules do not apply under `site/`; the repo's web rules (`~/.claude/rules/ecc/web/*`) do.

## Visual direction

Reference: typesafe.ai's register (high-contrast dark, clinical, numeric, a contrarian headline) plus a typing effect, since the product is a stream of hook events.

- Palette: near-black ground `#0b0d10`, off-white text `#e6e8eb`, muted `#8a919b`, verdict colors pass `#3ddc84`, flag `#f5b83d`, block `#ff5c5c`, interactive accent `#7aa2ff`. Tokens in `site/src/styles/tokens.css`; a light theme is not shipped in v1 (the page declares `color-scheme: dark`).
- Type: Inter for headings and prose, JetBrains Mono for every terminal, event, number, and command. Loaded from Google Fonts with real fallback stacks.
- Motion: typewriter with a blinking block cursor; verdict probabilities count up; timeline rows fade in. Everything honors `prefers-reduced-motion` by rendering the final state immediately. Only `transform` and `opacity` animate.
- No hero illustration. The terminal is the hero.

## Page structure (one scrolling page, five bands)

1. **Hero.** Headline: "The agent said the tests passed. They didn't." Subhead: one sentence on what agent-verdict does. Right side: a terminal that types out the "unreported failure" scenario in roughly eight seconds: the prompt, the failing `pytest` with `exit 1`, the assistant's claim, then the block line in red with the R1 reason. Below: the install command and a "replay a session" link that scrolls to band 2.
2. **Replay.** A scenario picker (tabs) and a player. Left: the timeline of hook events for the scenario, one row per event (prompt, pre, post, post_fail, stop), revealed step by step. Right: the verdict panel: the questions asked, each probability animating in, the rule table with the one that fired highlighted, the action, and the block reason exactly as the hook printed it. Controls: play, pause, step, restart. Scenarios shipped in v1:
   - `unreported-failure`: failing test, assistant claims success, R1 block.
   - `unbacked-check`: assistant says lint passed, no lint ran after the last edit, R2 block.
   - `honest-failure`: failing test, assistant reports it truthfully, pass.
   - `soft-failure`: command exits 0 but its output shows a failure, assistant claims success, R3 block.
   - `clean-pass`: tests run and pass, assistant reports it, pass with no provider call (no evidence gate).
3. **How it works.** Three columns: Record (hooks write a local ledger, nothing leaves), Judge (at Stop, a redacted summary goes to Jev with typed questions), Decide (rules R1 to R4, shadow or enforce). One diagram of the hook flow, rendered as inline SVG.
4. **Numbers.** Only measured values, each with its source command, in a monospace table: Stop p50 and p95 through OpenRouter (`scripts/bench_stop_live.py`), recorder p50 (`make bench-hook`), test count, hot-path coverage (`make coverage-hot`). One line in plain words: precision and recall are reported only after the labeled study (D-018), so none appear here yet. The values are read from `site/src/data/numbers.json`, which a script fills from the repo's recorded measurements, never typed by hand.
5. **Install and privacy.** The plugin install command, the mode and provider options, a four-sentence privacy paragraph matching `docs/PRIVACY.md`, links to the repo, PRIVACY.md, and CONSENT.md.

## Data pipeline

Two scripts produce everything the page shows; both are tested.

- `scripts/record_site_scenarios.py` (repo root, Python, billed, run by the owner or controller with the key). For each scenario in `site/scenarios/<name>/` (a tiny throwaway repo plus a `scenario.json` with the prompt and mode), it runs `claude -p` with the plugin in `shadow` mode against a temp `VERDICT_HOME`, exactly like `scripts/e2e_cheap.py --scenario`, and copies the resulting ledger to `site/scenarios/<name>/ledger.jsonl`. It refuses to run without `OPENROUTER_API_KEY` (exit 3). The unreported-failure and unbacked-check scenarios use a staged final message the same way `e2e_cheap.py` does, and the bundle records that the claim was staged.
- `site/scripts/build_replays.py` converts each ledger into `site/src/data/scenarios/<name>.json` with this closed shape:
  - `name`, `title`, `summary` (from `scenario.json`, hand-written, no ledger text), `staged_claim: bool`, `mode`, `recorded_at`, `claude_code_version`, `model_returned`.
  - `events[]`: `{seq, kind (prompt|pre|post|post_fail|stop|verdict|action), t_ms (offset from session start), tool, command (the ledger's sanitized `input_excerpt`, at most 80 chars), status, exit_code, decision, rule_id}`.
  - `final_message` (the ledger's redacted `final_message_excerpt`, at most 600 chars).
  - `questions[]`: `{key, type, statement (from the packaged question set), answer}`.
  - `decision`: `{action, would_have, rule_id, threshold_used, reason}` where `reason` is the hook's own block reason text.
  - No `out_head`, `out_tail`, `error_excerpt`, `prompt_excerpt`, paths, session ids, or hostnames.
- `site/scripts/check_replays.py` fails the build if any bundle has a key outside the shape, any string longer than its cap, or any string that `agent_verdict.verdict_hot.redact.redact` would change. It runs in `make check` and in the site's CI.
- `site/scripts/build_numbers.py` writes `site/src/data/numbers.json` from `docs/DECISIONS.md` D-033 and the latest `docs/measurements/` files, each entry carrying `value`, `unit`, `source_command`, and `measured_on`.

Bundles and numbers are committed, so a Vercel build needs no key and no Python.

## Site code

Astro, TypeScript, no UI framework. Two islands, everything else static:

- `Typewriter.ts`: given a script of lines `{text, delay_ms, class}`, types characters at a fixed cadence with a blinking block cursor; `reduced-motion` renders the finished transcript. Emits an event when done so the hero can reveal the install line.
- `ReplayPlayer.ts`: given a scenario bundle, exposes play, pause, step, restart; reveals timeline rows in `seq` order; when the `verdict` events arrive, animates each probability from 0 to its value; when the `action` arrives, highlights the rule row and prints the decision. State is a plain object; rendering is direct DOM updates; no dependencies.
- Components (`.astro`): `Hero`, `Terminal`, `ScenarioTabs`, `Timeline`, `VerdictPanel`, `HowItWorks`, `Numbers`, `Install`, `Footer`.
- Styles: `tokens.css`, `typography.css`, `global.css`; component styles scoped.

## Error handling

The page has no runtime inputs, so the only failures are build-time: a bundle failing `check_replays.py` fails the build; a missing font falls back to the stack; a browser without JavaScript sees the finished hero transcript and the first scenario's full timeline and verdict rendered statically (the islands enhance, they do not gate content).

## Deploy and CI

- Vercel: project root `site/`, framework Astro, static. Preview deploys on branches, production on `main`.
- GitHub Actions job `site` on pushes touching `site/**`: `npm ci`, `npm run check` (bundle check, type check, lint), `npm run build`, Playwright screenshots at 375, 768, 1440 plus a reduced-motion pass, Lighthouse with the repo's web budgets (LCP under 2.5 s, CLS under 0.1, landing JS under 150 KB gzipped; this page should be far under).

## Testing

- Vitest: `Typewriter` cadence and reduced-motion path; `ReplayPlayer` step order, probability animation end state, restart; bundle schema validation against a fixture.
- Python: `tests/test_site_replays.py` runs `check_replays.py` over the committed bundles and asserts the closed shape and redaction invariance; `build_numbers.py` output matches D-033.
- Playwright: screenshots and an accessibility scan (axe) per breakpoint; keyboard navigation of the scenario tabs and player controls.

## Open items for the owner

- The domain. Default is the Vercel-provided one until you pick a name.
- Whether to link the site from the README before or after the repo goes public.
