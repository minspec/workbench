#!/usr/bin/env bash
# board.sh — the MinSpec org board, measured at the moment of asking.
# Read-only. Lists, per repo: open PRs, branches ahead of default, live
# worktrees; then the dispatch log tail. Nothing here is cached or
# remembered — rerun it rather than trusting an old copy.
set -uo pipefail
ROOT="${MINSPEC_ROOT:-$HOME/projects/minspec}"
REPOS=(minspec .github skeleton docker recipes workbench-fixtures discussions workbench)
echo "== minspec org board — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
for r in "${REPOS[@]}"; do
    d="$ROOT/$r"
    [[ -d $d/.git ]] || { echo "-- $r: NOT CLONED"; continue; }
    def=$(git -C "$d" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|origin/||')
    echo "-- $r (default: ${def:-unknown})"
    gh pr list -R "minspec/$r" --state open --json number,title,isDraft,headRefName \
        --jq '.[] | "   PR #\(.number)\(if .isDraft then " [draft]" else "" end) \(.headRefName): \(.title)"' 2>/dev/null \
        || echo "   PRs: UNREACHABLE (gh failed — not zero, unknown)"
    git -C "$d" worktree list | sed 's/^/   wt /'
done
LOG="$ROOT/workbench/ops/dispatches/log.tsv"
echo "== dispatches (newest last)"
if [[ -f $LOG ]]; then tail -5 "$LOG" | sed 's/^/   /'; else echo "   none recorded"; fi
