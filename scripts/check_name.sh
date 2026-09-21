#!/bin/sh
# Gate G0.1. Exit 0 only if <name> is free on PyPI, npm, and as a GitHub user.
# Usage: scripts/check_name.sh <name>
set -u
name="${1:?usage: check_name.sh <name>}"
fail=0
code() { curl -s -m 15 -o /dev/null -w '%{http_code}' "$1"; }

command -v gh >/dev/null 2>&1 || { echo "gh unavailable" >&2; exit 1; }

pypi=$(code "https://pypi.org/pypi/${name}/json")
npm=$(code "https://registry.npmjs.org/${name}")
gh_output=$(gh api "users/${name}" 2>&1)
gh_status=$?
if [ "$gh_status" -eq 0 ]; then
  ghuser=taken
elif printf '%s' "$gh_output" | grep -qE 'HTTP 404|Not Found'; then
  ghuser=free
else
  ghuser=unreachable
fi

report() { printf '%-12s %s\n' "$1" "$2"; }
[ "$pypi" = "404" ] && report PyPI free || { report PyPI "taken or unreachable ($pypi)"; fail=1; }
[ "$npm" = "404" ] && report npm free || { report npm "taken or unreachable ($npm)"; fail=1; }
case "$ghuser" in
  free) report GitHub free ;;
  taken) report GitHub taken; fail=1 ;;
  *) report GitHub "unreachable"; fail=1 ;;
esac

echo "Existing repositories mentioning the name with claude (informational):"
gh search repos "${name} claude" --limit 5 --json fullName,description \
  --jq '.[] | "  \(.fullName): \(.description)"' 2>/dev/null || echo "  (search unavailable)"
exit "$fail"
