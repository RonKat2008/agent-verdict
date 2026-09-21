#!/bin/sh
# Drive headless Claude Code sessions so the capture plugin records every hook event.
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
export VERDICT_CAPTURE_DIR="$root/tests/fixtures/hooks/raw"
mkdir -p "$VERDICT_CAPTURE_DIR"
work=$(mktemp -d)
cd "$work" && git init -q .
run() {
  claude -p "$1" --plugin-dir "$root/scripts/fixture_capture_plugin" \
    --model haiku --max-turns 8 --permission-mode acceptEdits \
    --allowedTools "Bash,Write,Edit,Read,Agent" >/dev/null 2>"$work/stderr.log" \
    || echo "task exited non-zero (continuing): $1" >&2
}
run "Run this exact shell command and report its output: echo hello"
run "Run this exact shell command and tell me its exit code: sh -c 'echo boom >&2; exit 3'"
run "Create a file named note.txt containing the word hi. Then edit it so it says hello."
run "Use the Agent tool to start a general-purpose subagent whose only job is to run the shell command 'echo sub' and report the output. Then tell me what it reported."
echo "captured $(ls "$VERDICT_CAPTURE_DIR" | wc -l | tr -d ' ') payloads in $VERDICT_CAPTURE_DIR"
