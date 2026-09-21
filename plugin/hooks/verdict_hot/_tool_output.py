"""PostToolUse `tool_response` -> (text, out_kind), stripped of file/prompt
content (task-4 fix-round-1: Critical + Important).

`tool_output_text(tool_name, tool_response)` returns the text handed to the
recorder's normal normalize/redact/truncate pipeline, plus an `out_kind` tag
(`"structural"` or `"text"`) recorded on the row so a reader knows which
shape `out_head`/`out_tail` are in. It never returns the tool's raw file
content or an Agent delegation prompt:

- Write/Edit/NotebookEdit/Read/Glob/Grep (`out_kind="structural"`): an
  explicit-allowlist structural summary (file path, byte counts, hunk
  counts, line counts) built from the response BEFORE any text pipeline
  runs, then JSON-serialized. `NotebookEdit` has no captured fixture; its
  branch reuses Edit's field names and is unit-tested only against a
  synthetic payload (unverified against a real payload).
- Agent (`out_kind="text"`): only the subagent's RESULT text (the text of
  any returned content blocks) plus a small set of structural fields
  (`status`, `totalDurationMs`, `totalTokens`, `totalToolUseCount`, and the
  fields actually observed on the real fixture: `agentId`, `isAsync`,
  `canReadOutputFile`, `resolvedModel`, `outputFile`). `prompt` and
  `description` (already captured as this row's `input_excerpt`) are never
  included. No captured fixture shows a completed synchronous Agent
  response's content-block shape, so `_content_blocks_text` is written from
  the documented Anthropic content-block shape and is unit-tested only
  against a synthetic payload.
- Bash and everything else (`out_kind="text"`): unchanged for Bash
  (`stdout`/`stderr`, as before). Every other tool (WebFetch, MCP, unknown
  built-ins) keeps the prior compact-JSON-of-`tool_response` behavior, but
  first walks the structure and replaces any string value over 2,000
  characters stored under a key named `content`, `originalFile`,
  `oldString`, `newString`, `prompt`, `file`, `data`, `body`, or `text`
  (at any nesting depth) with `"[omitted N chars]"`.

Every string this module returns still goes through the caller's normal
normalize -> redact -> truncate pipeline; this module's job is only to keep
the *raw* input to that pipeline free of file contents and prompts before
`redact()` (a secret-shaped-pattern matcher, not a content-type filter) ever
sees it.
"""

from __future__ import annotations

import json

_OMIT_THRESHOLD = 2000
_DEFAULT_SENSITIVE_KEYS = frozenset(
    {"content", "originalFile", "oldString", "newString", "prompt", "file", "data", "body", "text"}
)

_AGENT_STRUCTURAL_KEYS = (
    "status",
    "totalDurationMs",
    "totalTokens",
    "totalToolUseCount",
    "agentId",
    "isAsync",
    "canReadOutputFile",
    "resolvedModel",
    "outputFile",
)


def json_compact(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _utf8_len(value: object) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def _write_summary(response: dict[str, object]) -> dict[str, object]:
    return {
        "filePath": response.get("filePath"),
        "type": response.get("type"),
        "userModified": response.get("userModified"),
        "content_bytes": _utf8_len(response.get("content")),
        "had_original": response.get("originalFile") is not None,
    }


def _edit_summary(response: dict[str, object]) -> dict[str, object]:
    patch = response.get("structuredPatch")
    return {
        "filePath": response.get("filePath"),
        "replaceAll": response.get("replaceAll"),
        "userModified": response.get("userModified"),
        "patch_hunks": len(patch) if isinstance(patch, list) else 0,
        "old_bytes": _utf8_len(response.get("oldString")),
        "new_bytes": _utf8_len(response.get("newString")),
    }


def _read_summary(response: dict[str, object]) -> dict[str, object]:
    file_obj = response.get("file")
    file_obj = file_obj if isinstance(file_obj, dict) else {}
    summary: dict[str, object] = {
        "filePath": file_obj.get("filePath"),
        "type": response.get("type"),
    }
    for key in ("numLines", "startLine", "totalLines"):
        if key in file_obj:
            summary[key] = file_obj[key]
    return summary


def _glob_or_grep_summary(response: dict[str, object]) -> dict[str, object]:
    return {key: response[key] for key in ("numFiles", "numMatches", "mode") if key in response}


_STRUCTURAL_BUILDERS = {
    "Write": _write_summary,
    "Edit": _edit_summary,
    "NotebookEdit": _edit_summary,  # no fixture observed; reuses Edit's shape
    "Read": _read_summary,
    "Glob": _glob_or_grep_summary,
    "Grep": _glob_or_grep_summary,
}


def _content_blocks_text(content: object) -> str:
    """Anthropic-style content-block text, joined. Never observed in a
    captured fixture (the real one is an async launch with no `content`);
    written from the documented shape, tested only synthetically."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _agent_output_text(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    result_text = _content_blocks_text(response.get("content"))
    structural = {key: response[key] for key in _AGENT_STRUCTURAL_KEYS if key in response}
    if not result_text and not structural:
        return ""
    return json_compact({"result": result_text, **structural})


def _sanitize_default_tree(value: object) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, val in value.items():
            is_oversized_sensitive = (
                key in _DEFAULT_SENSITIVE_KEYS
                and isinstance(val, str)
                and len(val) > _OMIT_THRESHOLD
            )
            if is_oversized_sensitive:
                result[key] = f"[omitted {len(val)} chars]"
            else:
                result[key] = _sanitize_default_tree(val)
        return result
    if isinstance(value, list):
        return [_sanitize_default_tree(item) for item in value]
    return value


def _default_output_text(response: object) -> str:
    return json_compact(_sanitize_default_tree(response))


def _bash_output_text(response: object) -> str:
    if not isinstance(response, dict):
        return json_compact(response)
    stdout = response.get("stdout")
    stderr = response.get("stderr")
    stdout_text = stdout if isinstance(stdout, str) else ""
    stderr_text = stderr if isinstance(stderr, str) else ""
    if stderr_text:
        return stdout_text + "\n[stderr]\n" + stderr_text
    return stdout_text


def tool_output_text(tool_name: str, tool_response: object) -> tuple[str, str]:
    """Returns `(text_for_pipeline, out_kind)`."""
    if tool_name == "Bash":
        return _bash_output_text(tool_response), "text"
    if tool_name == "Agent":
        return _agent_output_text(tool_response), "text"
    builder = _STRUCTURAL_BUILDERS.get(tool_name)
    if builder is not None and isinstance(tool_response, dict):
        return json_compact(builder(tool_response)), "structural"
    return _default_output_text(tool_response), "text"


def raw_output_bytes(tool_name: str, tool_response: object) -> int:
    """Byte length of the true original response, before any reduction.

    A size count leaks nothing (unlike the text this module otherwise
    returns), so it is measured against the real original payload even for
    tools whose stored text is now a reduced structural summary -- Bash
    keeps its prior stdout/stderr-only meaning unchanged.
    """
    if tool_name == "Bash":
        return len(_bash_output_text(tool_response).encode("utf-8"))
    return len(json_compact(tool_response).encode("utf-8"))
