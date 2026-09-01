#!/usr/bin/env bash
# test-guard-hook.sh — PostToolUse(Write|Edit): did that test file just plant a fault
# without proving it landed?
#
# test-guard.py existed for a whole session as a tool you had to remember to run, which by
# this toolchain's own record is the tier that does not work: the hooks stopped two real
# mistakes mid-command the same day a run-by-name tool sat unused while its exact failure
# happened again. So the check moved to the moment it matters — the instant a test file is
# written or edited.
#
# ADVISORY, never blocking. It reports; it does not deny. A test is often written in two
# passes (the plant first, the guard second), and a hook that refused the intermediate state
# would be noise, and noise gets switched off. And whatever it is handed — junk, no file, a
# file it cannot read — it exits 0. A hook that fails takes the session with it.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/test-guard.py"

payload=$(cat 2>/dev/null || true)
[ -n "$payload" ] || exit 0
[ -x "$GUARD" ] || exit 0

read -r tool file <<<"$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
t = d.get("tool_name") or ""
ti = d.get("tool_input") or {}
f = ti.get("file_path") or ""
# a path with whitespace would split across the read; drop those rather than mis-parse
if t and f and " " not in f and "\t" not in f:
    print(t, f)
' 2>/dev/null || true)"

case "${tool:-}" in
    Write|Edit|MultiEdit) ;;
    *) exit 0 ;;
esac
[ -n "${file:-}" ] && [ -f "$file" ] && [ -r "$file" ] || exit 0

findings=$(python3 "$GUARD" "$file" 2>/dev/null) || true
printf '%s' "$findings" | grep -q "unguarded-plant\|truncating-self-read" || exit 0

python3 - "$findings" <<'PY' 2>/dev/null || true
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext":
        "test-guard: this file plants a fault without proving the fault landed.\n"
        + sys.argv[1].strip()
        + "\n\nA plant that silently no-ops leaves a CLEAN fixture, so the check runs "
          "against a file with no fault in it and whichever way it answers is meaningless. "
          "Compare the fixture before and after, fail the case if it did not change, and "
          "fail it again if the file was clobbered — a truncating write does change the "
          "file, so a checksum alone will not catch it. A deliberate fixture carries "
          "`# test-guard: allow`.",
}}))
PY
exit 0
