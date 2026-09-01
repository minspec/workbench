#!/usr/bin/env bash
# context-precheck.sh — global PreToolUse hook. Asks "are you up to date?" before anything
# that leaves the machine. Silent for everything reversible. Never fails a call: any
# internal error exits 0 and the command proceeds.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
input=$(cat 2>/dev/null) || exit 0
[ -n "$input" ] || exit 0
# Two checks, cheapest first. unsafe-command is pure pattern matching with no network;
# context-precheck may fetch, so it runs only if nothing has already been refused.
refusal=$(printf '%s' "$input" | python3 "$DIR/unsafe-command.py" 2>/dev/null || true)
if [ -n "$refusal" ]; then
    printf '%s\n' "$refusal"
    exit 0
fi

printf '%s' "$input" | python3 "$DIR/context-precheck.py" 2>/dev/null || true
exit 0
