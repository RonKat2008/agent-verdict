#!/bin/sh
# Gate G0.1. Exit 0 only if <name> is free on PyPI, npm, and as a GitHub user.
# Usage: scripts/check_name.sh <name>
set -u
name="${1:?usage: check_name.sh <name>}"
fail=0
code() { curl -s -m 15 -o /dev/null -w '%{http_code}' "$1"; }

pypi=$(code "https://pypi.org/pypi/${name}/json")
npm=$(code "https://registry.npmjs.org/${name}")
if gh api "users/${name}" >/dev/null 2>&1; then ghuser=taken; else ghuser=free; fi

report() { printf '%-12s %s\n' "$1" "$2"; }
[ "$pypi" = "404" ] && report PyPI free || { report PyPI "taken or unreachable ($pypi)"; fail=1; }
[ "$npm" = "404" ] && report npm free || { report npm "taken or unreachable ($npm)"; fail=1; }
[ "$ghuser" = "free" ] && report GitHub free || { report GitHub taken; fail=1; }

echo "Existing repositories mentioning the name with claude (informational):"
gh search repos "${name} claude" --limit 5 --json fullName,description \
  --jq '.[] | "  \(.fullName): \(.description)"' 2>/dev/null || echo "  (search unavailable)"
exit "$fail"
