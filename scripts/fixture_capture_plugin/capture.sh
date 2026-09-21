#!/bin/sh
# Dump hook stdin to $VERDICT_CAPTURE_DIR and stay out of the way.
dir="${VERDICT_CAPTURE_DIR:-}"
[ -n "$dir" ] || exit 0
mkdir -p "$dir"
cat > "$dir/$1-$(date +%s)-$$.json"
exit 0
