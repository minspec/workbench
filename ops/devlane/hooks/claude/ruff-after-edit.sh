#!/usr/bin/env bash
# ruff-after-edit.sh — a PostToolUse hook: did the edit I just made leave ruff check unhappy?
#
# `ruff.toml` makes `ruff check` the contract and `ruff format` not. A formatter complaint
# is advice and never a failure; this hook nags only on lint, at the moment of the edit,
# because the gate runs later.
#
# ADVISORY, ALWAYS. It exits 0 on everything: junk input, missing files, no ruff, its own errors,
# and a ruff that cannot run. Work arrives half-finished and a hook that interrupts that gets
# switched off, at which point it protects nothing.

set -uo pipefail

# The corpus lives in `ops/devlane/hooks/tests/`. `--test` is a pointer, not a runner.
if [ "${1:-}" = "--test" ]; then
    printf 'corpus moved to ops/devlane/hooks/tests/\n' >&2
    exit 2
fi

payload=$(cat 2>/dev/null) || exit 0
[ -n "$payload" ] || exit 0

field() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

file=$(field '.tool_response.filePath')
[ -n "$file" ] || file=$(field '.tool_input.file_path')
[ -n "$file" ] || file=$(field '.tool_input.relative_path')     # Serena's symbol/regex editors
[ -n "$file" ] || exit 0
case "$file" in *.py) ;; *) exit 0 ;; esac

# Serena's path is relative to the project root, so resolve against the session cwd
if [ ! -f "$file" ]; then
    base=$(field '.cwd'); [ -n "$base" ] || base=$PWD
    file="$base/$file"
fi
[ -f "$file" ] || exit 0

root=$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -n "$root" ] || exit 0

# A repo that has not configured ruff has not asked for this. `[tool.ruff` rather than a bare
# pyproject.toml: nearly every Python project has the latter and most do not use ruff.
configured=0
grep -qs '^\[tool\.ruff' "$root/pyproject.toml" && configured=1
[ -f "$root/ruff.toml" ] || [ -f "$root/.ruff.toml" ] && configured=1
[ "$configured" = 1 ] || exit 0

if command -v ruff >/dev/null 2>&1; then
    run_ruff() { ruff "$@"; }
elif command -v uv >/dev/null 2>&1; then
    run_ruff() { (cd "$root" && uv run --quiet ruff "$@"); }
else
    exit 0
fi

# Prove ruff RUNS before reading its verdict. `uv run` in a directory that is not a uv project
# fails, and a failure to run looks identical to "this file is clean" if you only check stdout
# — silence is what we want when ruff cannot run. CI is the gate.
run_ruff --version >/dev/null 2>&1 || exit 0

rel=${file#"$root"/}
lint=$(run_ruff check --quiet "$file" 2>/dev/null | head -5)
[ -n "$lint" ] || exit 0

detail=$(printf '\n%s' "$lint")

jq -nc --arg r "$rel" --arg d "$detail" '{
  hookSpecificOutput: {
    hookEventName: "PostToolUse",
    additionalContext: ("[ruff] \($r) fails ruff check after that edit.\($d)\n\nFix it now rather than at commit time:\n    ruff check \($r)\n\nThis is advisory. CI is the lint gate; this only says so at the moment of the edit.")
  }
}' 2>/dev/null || true
exit 0
