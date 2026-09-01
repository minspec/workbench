#!/usr/bin/env bash
# context-boundary.sh — global PostToolUse hook. Says so when the ground moves.
#
# Context rot is not gradual: it happens at instants. A checkout, a rebase, a fetch that
# moves a ref, a build — each silently expires every measurement taken before it, and the
# command reports success either way.
#
# The decision of what counts as a boundary lives in boundary-match.py; its corpus lives
# in `ops/devlane/hooks/tests/`. Precision matters more than
# coverage: a hook that fires during ordinary work is read as noise and then ignored, which
# is worse than not having one.
#
# This never blocks and never fails a tool call. It exits 0 in every path, including when
# python is missing, the payload is malformed, or the repo is not a git repo.

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

input=$(cat 2>/dev/null) || exit 0
[ -n "$input" ] || exit 0

result=$(printf '%s' "$input" | python3 "$DIR/boundary-match.py" 2>/dev/null) || exit 0
[ -n "$result" ] || exit 0

kind=${result%%$'\t'*}
note=${result#*$'\t'}

# Record before warning. The warning helps this session; the record is what the next one
# reads instead of rebuilding state from whatever happens to be visible.
"$DIR/context-stream.sh" record "$kind" >/dev/null 2>&1 || true

python3 - "$note" <<'PY' 2>/dev/null || true
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Context boundary crossed — " + sys.argv[1]
        + " Re-take before reusing; do not reason from what you measured earlier.",
}}))
PY
exit 0
