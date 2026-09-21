# Fixture provenance

Captured 2026-09-21 with `2.1.278 (Claude Code)` by `make capture-fixtures`.
Payloads are real hook stdin, sanitized by `scripts/process_fixtures.py`.
Captures ran with the machine owner's global hooks active (not just this
project's capture plugin), which is why the raw payload set (git-ignored,
not part of this fixture set) contains orphan `PreToolUse` payloads with
no matching `PostToolUse`/`PostToolUseFailure` (see `docs/VERIFIED_FACTS.md`
row G15).

- `post_tool_use_bash.json`
- `post_tool_use_write.json`
- `post_tool_use_read.json`
- `post_tool_use_edit.json`
- `post_tool_use_agent.json`
- `post_tool_use_failure_bash.json`
- `pre_tool_use_bash.json`
- `pre_tool_use_write.json`
- `pre_tool_use_read.json`
- `pre_tool_use_edit.json`
- `pre_tool_use_agent.json`
- `session_end.json`
- `session_start.json`
- `stop.json`
- `subagent_stop.json`
- `user_prompt_submit.json`
