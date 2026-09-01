#!/usr/bin/env bash
# link-ops.sh — make the ops lane reachable from every sibling MinSpec
# clone without touching tracked files: an untracked `ops` symlink in
# each clone, excluded via .git/info/exclude (per-clone, shared by all
# of that clone's worktrees, never committed). Idempotent.
set -uo pipefail
ROOT="${MINSPEC_ROOT:-$HOME/projects/minspec}"
OPS="$ROOT/workbench/ops"
[[ -d $OPS ]] || { echo "link-ops: refusal: expected ops at $OPS; found none; merge or check out the ops lane first" >&2; exit 2; }
for r in minspec .github skeleton docker recipes workbench-fixtures discussions; do
    d="$ROOT/$r"
    [[ -d $d/.git ]] || { echo "$r: not cloned, skipped"; continue; }
    ln -sfn "$OPS" "$d/ops"
    excl="$d/.git/info/exclude"
    grep -qxF '/ops' "$excl" 2>/dev/null || echo '/ops' >> "$excl"
    echo "$r: ops -> $OPS (excluded locally)"
done
