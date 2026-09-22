#!/bin/sh
# POSIX launcher for the agent-verdict hook entry point (task-5-brief.md).
#
# Interpreter resolution order: $VERDICT_PYTHON, else the path recorded in
# "$VERDICT_HOME/interpreter" (or "$HOME/.verdict/interpreter") if that file
# exists and names an executable path, else "python3" on PATH, else exit 0
# silently rather than surface a visible "hook error" notice for a missing
# interpreter.
#
# D-019: pass "-S" only, never "-E" -- "-E" alone measured about 67ms of
# extra stdlib import cost on macOS's system python3 (E1). "-S" already
# blocks site-packages and keeps the script directory at sys.path[0].
#
# Never prints anything, on any path: stdout here could be read by Claude
# Code as a JSON hook decision on any exit code (VERIFIED_FACTS A15).

if [ -n "${VERDICT_DISABLE:-}" ]; then
    exit 0
fi

unset PYTHONPATH
unset PYTHONHOME
unset PYTHONSTARTUP

home="${VERDICT_HOME:-$HOME/.verdict}"
py=""

if [ -n "${VERDICT_PYTHON:-}" ]; then
    py="$VERDICT_PYTHON"
else
    interpreter_file="$home/interpreter"
    if [ -f "$interpreter_file" ]; then
        candidate="$(cat "$interpreter_file" 2>/dev/null)"
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            py="$candidate"
        fi
    fi
    if [ -z "$py" ]; then
        py="$(command -v python3 2>/dev/null)"
    fi
fi

if [ -z "$py" ]; then
    exit 0
fi

script_dir="$(dirname "$0")"

exec "$py" -S "$script_dir/verdict_hook.py" "$@"
