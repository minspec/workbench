#!/usr/bin/env python3
"""Decide whether a shell command crossed a context boundary.

Reads a PostToolUse payload on stdin, prints "<kind>\t<note>" if it did, nothing if not.

Precision matters more than coverage: a hook that fires during ordinary work is read as
noise and then ignored, which is worse than not having one. So matching looks at COMMAND
POSITIONS rather than substrings — an earlier version globbed '*build*' and fired on
`ls build/`, `cat docs/build-notes.md` and `cd builder`.

The corpus lives in `ops/devlane/hooks/tests/`.
"""

import json
import logging
import os
import re
import sys

# `.claude/settings.json` runs every hook in this directory by path, so a sibling import is
# free — but only once the directory is on the path, which it is not when python reads the
# script from stdin.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from command_shape import commands

RULES = [
    ("tree",
     r"^git\s+(?:checkout|switch)\s+(?!--\s)|^git\s+worktree\s+(?:add|remove)\b",
     ("the working tree changed. File contents, greps, line numbers and anything read from "
     "the tree before this are stale. Build output belongs to the PREVIOUS checkout — "
     "rebuild before testing against it, and expect dangling symlinks where generated "
     "trees differ between branches.")),
    ("history",
     (r"^git\s+rebase\b|^git\s+commit\b.*--amend|^git\s+push\b.*(?:--force|-f)\b"
     r"|^git\s+filter-repo\b"),
     ("history was rewritten. Commit SHAs, counts, diffs, and any PR with this branch as "
     "its head are stale. A PR does NOT recompute when its BASE moves, only when its head "
     "does — verify with 'gh pr diff <n> --name-only', never the compare API, which is "
     "computed live and cannot observe the failure.")),
    ("refs",
     r"^git\s+(?:fetch|pull)\b|^git\s+remote\s+update\b",
     ("refs may have moved. Ahead/behind, merge-base, 'is it up to date', and every "
     "distance computed earlier are stale.")),
    ("discard",
     r"^git\s+(?:reset|stash|clean)\b|^git\s+(?:restore|checkout)\s+--\s",
     ("working-tree state was discarded. Confirm what survived with 'git status --short' "
     "before trusting anything staged, written or measured — untracked files are gone, and "
     "no reflog holds them.")),
    ("artifacts",
     (r"^(?:make|cmake|ninja|gradle|mvn|sphinx-build|tsc|webpack|vite)\b"
     r"|^(?:docker|cargo|go|dotnet|bazel|zig|swift)\s+build\b"
     r"|^(?:pytest|jest|vitest|playwright|tox|nox)\b"
     r"|^(?:cargo|go|dotnet|swift|npm|yarn|pnpm)\s+test\b"
     r"|^[\w./-]*\b(?:doc-)?build\b\s*(?:-|$)"),
     ("artifacts or results were regenerated. Counts, listings and test outcomes taken "
     "before this run describe the previous state.")),
]


def classify(cmd: str):
    """The first boundary any command position in the text crosses, or None.

    Where the command positions ARE is `command_shape`'s question. This file used to answer
    it with its own runner list, and that list contained `npm`, `yarn` and `pnpm` — so
    `npm test` was peeled to `test` before the `npm test` rule two lines below could be
    tried, and the rule never fired for the launchers it names. It also had no idea what a
    heredoc was, and recorded a working-tree crossing for the words `git checkout` written
    inside a note.
    """
    for seg in commands(cmd):
        for kind, pattern, note in RULES:
            if re.search(pattern, seg):
                return kind, note
    return None


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("corpus moved to ops/devlane/hooks/tests/", file=sys.stderr)
        sys.exit(2)
    try:
        payload = json.load(sys.stdin)
        command = ((payload.get("tool_input") or {}).get("command") or "")[:2000]
    except Exception:
        logging.exception("failed to read hook payload from stdin")
        sys.exit(0)
    hit = classify(command)
    if hit:
        print(hit[0] + "\t" + hit[1])
