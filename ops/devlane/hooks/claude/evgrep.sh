#!/usr/bin/env bash
# evgrep.sh -- truncation-honest evidence grep. The COUNT comes from the full search; only
# the DISPLAY is bounded, and truncation announces itself instead of ending silently.
#
# Exists because `grep "a\|b\|c\|d" file | head -8` answered a completeness question with
# its first eight lines: the match that mattered sat below the clip, the conclusion drawn
# was "absent", and nothing looked wrong. Bound the display, never the measurement.
#
# usage: evgrep.sh [grep options] PATTERN PATH...      (args pass through to grep -n)
#        EVGREP_LIMIT=40 evgrep.sh ...                 (default display limit: 20)

set -uo pipefail
LIMIT=${EVGREP_LIMIT:-20}

out=$(grep -n "$@" 2>&1)
rc=$?
if [ "$rc" -ge 2 ]; then
    printf '%s\n' "$out" >&2
    exit "$rc"
fi

n=$(printf '%s' "$out" | grep -c .)
printf '== %s match(es)\n' "$n"
[ "$n" -gt 0 ] && printf '%s\n' "$out" | head -n "$LIMIT"
if [ "$n" -gt "$LIMIT" ]; then
    printf '== TRUNCATED: %s more match(es) not shown — do not conclude absence or completeness from this view\n' $((n - LIMIT))
fi
exit "$rc"
