#!/usr/bin/env python3
"""Refuse two command shapes that fail silently, and say what to write instead.

Both were rules in CLAUDE.md before they were checks here, and both were then broken dozens
of times in the session that produced this file. That is what makes them nightmares rather
than mistakes: knowing better is not the same as being stopped.

  1. A test or build runner clipped to a handful of lines with no failure grep.
     `playwright test | tail -4` printed "1 skipped / 71 passed" and cut "61 failed" off the
     top. The summary block is several lines and the failure count is FIRST, so a short tail
     reliably removes exactly the number that matters.

  2. A string-replace whose result is written, with nothing asserting the anchor matched.
     `s.replace(old, new)` returns the input unchanged when `old` is absent. The write
     succeeds, the exit code is 0, and the next command runs against the unedited file.

Neither refusal costs more than a retry, and both name the fix. The corpus lives in
`ops/devlane/hooks/tests/`.
"""

import json
import logging
import os
import re
import subprocess
import sys

# `.claude/settings.json` runs every hook in this directory by path, so a sibling import is
# free — but only once the directory is on the path, which it is not when python reads the
# script from stdin.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from command_shape import commands, statements, strip_data_heredocs

# ------------------------------------------------------------------------- amend
#
# 3. `git commit --amend` absorbing an index nobody inspected.
#    --amend commits whatever is staged and prints one sha. Twice in one session a doc
#    build left 48 generated .rst files in the tree; the first time `git add -A` staged
#    them (7 files became 56), the second time they were still staged from before, so
#    even `git add CHANGELOG.md` produced 61. Both were caught only because the file
#    count was printed afterwards and looked wrong.
#
# 4. `git commit --amend` while HEAD is a commit that is already upstream.
#    Amending there does not add a commit, it REWRITES the one HEAD points at — which
#    was the maintainer's. The tell was an unrelated file appearing as "new" in the
#    branch diff. Unambiguously wrong, so this one refuses rather than asks.

AMEND = re.compile(r"\bgit\b[^;&|\n]*?\bcommit\b[^;&|\n]*?--amend\b")
# an amend of a couple of files is ordinary; an amend of a dozen is a sweep
AMEND_STAGED_LIMIT = 12
PUBLISHED_REFS = ("upstream/main", "upstream/master", "origin/main", "origin/master")


def is_amend(cmd: str) -> bool:
    """True only where git is the command being run.

    `grep -n 'git commit --amend' notes.md` mentions one; it does not make one. That is the
    same proxy-for-the-thing mistake the rest of this file exists to catch, so the test is
    positional: git must be the program the position invokes.

    `command_shape` is what decides where a position starts and what its program is. The
    hand-rolled version here dropped UPPERCASE env assignments only, so `flags=1 git commit
    --amend` was not an amend; and it required `git` literally first, so `sudo git commit
    --amend` and `for b in a b; do git commit --amend; done` were not either.
    """
    return any(c.startswith("git ") and AMEND.search(c) for c in commands(cmd))


def _ok(args: list[str], timeout: int = 8) -> bool:
    """Exit status only — for queries like `merge-base --is-ancestor` that answer with it."""
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False).returncode == 0  # noqa: S603 — literal executable, list argv, no shell; the only non-literal elements are the -C operand (the hook's own cwd) and a ref name from PUBLISHED_REFS
    except Exception:
        logging.exception("git query failed; treating as not-ok")
        return False


def head_already_published(cwd: str) -> str | None:
    """The published ref that already contains HEAD, if any.

    `--is-ancestor` is reflexive, so HEAD sitting exactly on upstream/main answers yes —
    which is the case that caused the incident.
    """
    for ref in PUBLISHED_REFS:
        if _ok(["git", "-C", cwd, "rev-parse", "--verify", "--quiet", ref]) and \
           _ok(["git", "-C", cwd, "merge-base", "--is-ancestor", "HEAD", ref]):
            return ref
    return None


def staged_summary(cwd: str) -> tuple[int, str]:
    """How many files an amend would carry, and where they are."""
    out = _run(["git", "-C", cwd, "diff", "--cached", "--name-only"])
    files = [f for f in out.splitlines() if f.strip()]
    tops: dict[str, int] = {}
    for f in files:
        key = "/".join(f.split("/")[:2]) if "/" in f else f
        tops[key] = tops.get(key, 0) + 1
    top = sorted(tops.items(), key=lambda kv: -kv[1])[:4]
    return len(files), ", ".join(f"{k} ({n})" for k, n in top)


def _run(args: list[str], timeout: int = 8) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 — literal executable (git), list argv, no shell; the only non-literal element is the -C operand (the hook's own cwd)
    except Exception:
        logging.exception("git query failed; treating as empty output")
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


# This list was a subset of the runners actually in use — `poe doc-build` was here and
# `poe test`, the command this project runs its suite with, was not. Found by mutation-testing
# against real commands, not by reading the regex, and it is the "subset of an unenumerated
# set" class committed inside the tool that refuses two other members of it.
#
# So: any `poe <task>`, and the common suite runners. Task runners are enumerated by their
# LAUNCHER (poe, make, tox, npm, uv run) rather than by task name, because task names are
# per-project and a per-project list is the same bug again.
# Linters and formatters are excluded deliberately: they print their verdict at the END
# ("All checks passed", "Found N errors"), so a short tail keeps it. The failure this rule
# exists for is a TEST runner, whose summary block puts the failure count FIRST.
RUNNER = re.compile(r"\b(playwright test|pytest|poe\s+(?!lint|format|fmt|type)[a-z][\w-]*|sphinx-build|selftest\.sh|"
                    r"mutation-check\.sh|mutation-live\.sh|npm (?:test|run test)|cargo test|"
                    r"go test|jest|vitest|tox|make\s+(?:test|check|lint)|rspec|phpunit)\b")
CLIPPED = re.compile(r"\|\s*(?:tail|head)\s+-(\d+)\b")
# `[^|]*` here was a bug: `grep -iE "warning|error"` contains a pipe INSIDE the quoted
# pattern, so the class stopped before reaching the word that made it safe. Look for a
# grep and a failure word anywhere in the command instead.
FAIL_GREP = re.compile(r"grep(?s:.)*?(fail|passed|error|✘|✗)", re.IGNORECASE)

# A multi-alternation grep at the head of a pipeline is a COMPLETENESS question — "does any
# of these appear?" — and a head/tail on it silently truncates the answer. A head -8 on
# exactly that shape ended before nixd_ls.py:114 (where which("nix") raises), and the
# absence conclusion drawn from the first eight matches was wrong. Same class as the
# 30-per-page GitHub listing read as a total. Scope, tuned on the session's real commands:
# grep must START the statement (a mid-pipeline grep is a filter — `pytest | grep -E
# 'failed|passed' | tail` is the SANCTIONED summary shape and must stay quiet), and the
# pattern must carry >=2 alternatives-separators inside quotes (single-needle greps piped
# to head are region views; `grep -rn foo src | head -5` stays ordinary work).
SWEEP_GREP = re.compile(r"^\s*grep\b")


def quoted_pipe_count(stmt: str) -> int:
    """Count '|' characters inside quoted spans — alternation separators in a grep pattern,
    whether basic (a\\|b) or extended (-E 'a|b')."""
    count, quote = 0, None
    for ch in stmt:
        if quote:
            if ch == quote:
                quote = None
            elif ch == "|":
                count += 1
        elif ch in "'\"":
            quote = ch
    return count


WRITES = re.compile(r"write_text\(|open\([^)]*['\"][wa]['\"]|\.writelines\(|>\s*\$?\w*\.py\b")
REPLACE = re.compile(r"\.replace\(|re\.sub\(|\.subn\(")
GUARDED = re.compile(r"\bassert\b|\braise\b|if\s+\w+\s+not\s+in\b|count\s*==|!=\s*s\b")

CLIP_LIMIT = 6


# A pull request carries feedback in FOUR API objects. A loop that waits for a verdict while
# watching a subset reports "still running" with the answer already sitting on the surface it
# does not read — forty minutes the first time, and again today when a clean Codex verdict
# arrived as an issue comment and the poll was watching reviews, inline comments and
# reactions. Both times the tool that reads all four already existed.
POLL_LOOP = re.compile(r"\bwhile\b[^\n]*?\bdo\b|\bfor\b[^\n]*?\bdo\b|\bsleep\s+\d+")
SURFACES = {
    "conversation comments (issues/N/comments)": re.compile(r"issues/[^/\s]+/comments\b"),
    "review bodies (pulls/N/reviews)": re.compile(r"pulls/[^/\s]+/reviews\b"),
    "inline review comments (pulls/N/comments)": re.compile(r"pulls/[^/\s]+/comments\b"),
    "reactions": re.compile(r"/reactions\b"),
}

# `statusCheckRollup` is an APPEND-ONLY list of check-run attempts against the head sha, not
# a state. A re-run does not replace the attempt it supersedes; it appends beside it. On
# oraios/serena#1873 the field held 47 entries for 24 distinct check names, with a FAILURE
# from 05:27:14Z sitting next to the SUCCESS from 16:11:18Z that cleared it. Filtering it for
# FAILURE answers "did any attempt ever fail here", which reads exactly like "is this red
# now" -- and reported five green PRs as red, contradicted by the user looking at the UI.
# `gh pr checks` and the web UI collapse to the latest attempt per name; nothing else does.
# Prose handed to a DOUBLE-QUOTED shell argument is shell input, not text. Backticks inside it
# run as command substitution: on 2026-08-18 a commit message containing a backticked flag name
# executed it, the command failed, and its EMPTY output replaced the phrase. The commit succeeded
# and printed a sha; the message on disk (still at 4633f78 in the reflog) had a hole in it, and it
# was found only by grepping afterwards for a phrase I remembered writing. Worse is the silent
# case: a substitution that SUCCEEDS injects its output into the prose with no error at all.
#
# Only backticks are refused, on purpose. `$(...)` is the idiomatic substitution form and is
# normally deliberate (`-m "release $(cat VERSION)"`), and a bare `$VAR` in prose is common enough
# that flagging it would be noise. Backticks are archaic as substitution and ubiquitous as
# markdown code spans, so the intent is not ambiguous. The other two are a declared gap.
QUOTED_PROSE = re.compile(r'(?:-m|--message|--body)\s+"((?:[^"\\]|\\.)*)"')

ROLLUP = re.compile(r"statusCheckRollup")
PRINTS_DATA = re.compile(r"^\s*(?:echo|printf)\b")
ROLLUP_VERDICT = re.compile(r"\.conclusion\b|[\"']conclusion[\"']")
# an explicit collapse to one attempt per name is the correct use and must stay usable.
# `group_by(.conclusion)` is NOT that -- it is the incident's own second shape.
ROLLUP_DEDUPED = re.compile(r"(?:group_by|unique_by|sort_by)\s*\(\s*\.(?:name|context)\b|"
                            r"max_by\s*\(\s*\.(?:startedAt|completedAt)\b|"
                            r"latest_per_check")

# Blanking data heredocs — a commit message that DESCRIBES an unguarded `.replace()` is not
# one — moved into `command_shape`, along with the fix to what "this heredoc executes" means.
# The test here was `\b(python3?|…|bash|sh|…)\b` against the whole head, and `\bsh\b` matched
# the `sh` in a FILENAME: `cat > setup.sh <<'EOF'` and `tee notes.sh <<'EOF'` were read as
# running their bodies. In this repo that refused a memory-file write, because the file was
# named `…-bash.md`.


def check(cmd: str):
    """Return (kind, message) if the command should be refused, else None."""
    if not cmd or not cmd.strip():
        return None
    cmd = strip_data_heredocs(cmd)

    # The clip has to be in the SAME pipeline as the runner. Matching "runner anywhere" plus
    # "clip anywhere" flagged `poe doc-build > log; grep ... | head -3`, where the head is on
    # a grep of markdown and the runner's output went to a file. A checker that blocks real
    # work gets switched off, so precision matters more here than reach.
    m = None
    # `statements()` splits on statement separators only and keeps a PIPELINE whole — a bare
    # & also appears inside `2>&1`, and splitting there tore `playwright test ... 2>&1 |
    # tail -4` into two halves, putting the runner in one and the clip in the other.
    # A runner's NAME passed to a file-reading command is a string, not an invocation:
    # `grep -n foo tools/selftest.sh | head -5` reads a file and was refused for it. The
    # same proxy-for-the-thing mistake the rest of this toolchain exists to catch.
    READS_FILES = re.compile(r"\b(grep|rg|ls|cat|wc|find|stat|chmod|rm|cp|mv|sed|awk|git|"
                             r"head|tail|diff|cksum|md5sum|realpath|dirname|basename)\b")

    for stmt in statements(cmd):
        if not RUNNER.search(stmt) or FAIL_GREP.search(stmt):
            continue
        # only consider a clip that appears after the runner within this statement
        rpos = RUNNER.search(stmt).start()
        if READS_FILES.search(stmt[:rpos]):
            continue                        # the runner is an argument, not the command
        for c in CLIPPED.finditer(stmt):
            if c.start() > rpos:
                m = c
                break
        if m:
            break
    if m and int(m.group(1)) <= CLIP_LIMIT:
        return ("clipped-runner",
                (f"This clips a test/build runner to {m.group(1)} lines with nothing grepping for "
                "the failure count. Runner summaries put 'N failed' FIRST, so a short tail cuts "
                "exactly the line that matters — that is how '61 failed' got read as a pass.\n\n"
                "Write it so the failure count cannot be dropped, e.g.\n"
                "    ... 2>&1 | grep -E 'failed|passed|skipped' | tail -3\n"
                "or keep the full output and say why you need it."))

    for stmt in statements(cmd):
        if not SWEEP_GREP.search(stmt):
            continue
        clip = CLIPPED.search(stmt)
        if clip and quoted_pipe_count(stmt[: clip.start()]) >= 2:
            return ("clipped-sweep",
                    ("This clips a multi-alternation grep — a completeness question (\"does any of "
                    "these appear?\") whose answer head/tail truncates SILENTLY. A head -8 on exactly "
                    "this shape ended before the line that mattered, and the absence conclusion drawn "
                    "from the first eight matches was wrong. Same class as a 30-per-page API listing "
                    "read as a total.\n\n"
                    "Count first, then look:\n"
                    "    grep -c PATTERN file                     # the verdict, untruncatable\n"
                    "    ops/devlane/hooks/claude/evgrep.sh PATTERN file   # count header + bounded view + loud truncation marker\n"
                    "or take the full output and say why you need it."))

    # only the shape that WRITES: an unwritten replace is harmless
    if REPLACE.search(cmd) and WRITES.search(cmd) and not GUARDED.search(cmd):
        return ("unguarded-replace",
                ("This replaces text and writes the result with nothing checking the anchor "
                "matched. `.replace()` returns the input unchanged when the pattern is absent: "
                "the write succeeds, the exit code is 0, and the next command runs against an "
                "unedited file. That happened three times in one session.\n\n"
                "Guard it, e.g.\n"
                "    assert old in s, 'anchor missing'\n"
                "    s = s.replace(old, new, 1)\n"
                "or use re.subn and assert the count."))

    # a wait-loop over SOME of a PR's feedback surfaces
    if "gh " in cmd and POLL_LOOP.search(cmd):
        present = [n for n, p in SURFACES.items() if p.search(cmd)]
        missing = [n for n in SURFACES if n not in present]
        if present and missing:
            return ("partial-feedback-poll",
                    "This waits for PR feedback while watching "
                    f"{len(present)} of {len(SURFACES)} surfaces. Not watched:\n"
                    + "".join(f"    - {n}\n" for n in missing)
                    + "\nAn automated reviewer answers on whichever surface suits it: findings "
                    "arrive as a review plus inline comments, a clean verdict as a conversation "
                    "comment or a 👍 reaction. A poll that reports 'still running' while the "
                    "answer sits on an unwatched surface answers the question wrongly rather "
                    "than not answering it.\n\n"
                    "Use the tool that reads all four:\n"
                    "    ops/devlane/hooks/claude/pr-feedback.sh <pr> [repo]            one report\n"
                    "    ops/devlane/hooks/claude/pr-feedback.sh <pr> [repo] --watch    wait for a change")

    # a rollup read reduced to a pass/fail verdict, with nothing collapsing the attempts
    for stmt in statements(cmd):
        r = ROLLUP.search(stmt)
        # the field NAME ahead of the token means it is a string being read from a file or
        # printed -- not a request being made. `echo '<the bad command>' | python3 hook.py`
        # is how this hook is tested, and it refused that on the day it was written.
        if not r or READS_FILES.search(stmt[: r.start()]) or PRINTS_DATA.search(stmt[: r.start()]):
            continue
        if not ROLLUP_VERDICT.search(stmt) or ROLLUP_DEDUPED.search(stmt):
            continue
        return ("stale-check-rollup",
                ("This reads `statusCheckRollup` and reduces it to a pass/fail verdict. That "
                "field is an APPEND-ONLY list of check-run ATTEMPTS against the head sha, not "
                "the current state: a re-run appends beside the attempt it supersedes rather "
                "than replacing it. On oraios/serena#1873 it carried 47 entries for 24 check "
                "names, with a FAILURE from 05:27:14Z next to the SUCCESS from 16:11:18Z that "
                "cleared it.\n\n"
                "So `select(.conclusion==\"FAILURE\")` answers \"did any attempt ever fail "
                "here\", which reads identically to \"is this red now\". It reported 18 failing "
                "checks across 5 PRs that were all green, and the user had to correct it.\n\n"
                "Ask the question you mean. `gh pr checks` collapses to the latest attempt "
                "per name, exactly as the web UI does -- verified: 24 rows for the 47-entry "
                "rollup above.\n"
                "    gh pr checks <pr> --repo <o/r>\n"
                "    gh pr checks <pr> --repo <o/r> --json name,state,bucket   # scriptable;\n"
                "        bucket is already pass/fail/pending/skipping/cancel\n"
                "or, only when you must batch many PRs in one call, collapse them yourself:\n"
                "    --jq '.statusCheckRollup | group_by(.name) | map(max_by(.startedAt))\n"
                "          | map(select(.conclusion==\"FAILURE\"))'"))

    # a message or body passed as a double-quoted string, with backticks inside it.
    # Statement-scoped so that `echo '<the bad command>' | python3 hook.py` -- which is how this
    # rule gets tested -- is read as printing the shape rather than committing it.
    # NOT split into statements: a prose message spans newlines, and splitting on them tore
    # `-m "line one\n\nline two with a `backtick`"` into fragments that matched nothing -- losing
    # the long-message case, which is the one this rule exists for. PRINTS_DATA is anchored at the
    # start of the command, which is enough to tell `echo '<the shape>' | python3 hook.py` apart
    # from a real commit. A command that echoes FIRST and then commits is a declared blind spot.
    if not PRINTS_DATA.search(cmd):
        for quoted in QUOTED_PROSE.finditer(cmd):
            if "`" not in quoted.group(1):
                continue
            return ("shell-expanded-prose",
                    ("This passes prose as a DOUBLE-QUOTED shell argument, and the prose contains "
                    "backticks. The shell runs what is between them and substitutes the output — so "
                    "the text you wrote is not the text that gets committed or posted.\n\n"
                    "That happened on 2026-08-18: a commit message containing a backticked flag name "
                    "executed it, the command failed, and its empty output REPLACED the phrase. The "
                    "commit succeeded and printed a sha; the message on disk had a hole in it, and it "
                    "was found only by grepping later for a phrase I remembered writing. A "
                    "substitution that SUCCEEDS is worse — it injects output into your prose with no "
                    "error at all.\n\n"
                    "Pass prose in a form the shell does not read:\n"
                    "    git commit -q -F - <<'EOF'      # quoted heredoc — no expansion at all\n"
                    "    ...your message...\n"
                    "    EOF\n"
                    "    git commit -q -F <file>          # or write it to a file first\n"
                    "    git commit -m 'single quotes'    # for a one-liner with no substitution\n\n"
                    "Declared gap: `$(...)` and `$VAR` are NOT refused here, because both are "
                    "normally deliberate. Only backticks are unambiguous."))
    return None


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("corpus moved to ops/devlane/hooks/tests/", file=sys.stderr)
        sys.exit(2)
    try:
        payload = json.load(sys.stdin)
        cmd = ((payload.get("tool_input") or {}).get("command") or "")[:4000]
    except Exception:
        logging.exception("failed to read hook payload from stdin")
        sys.exit(0)
    hit = check(cmd)

    # An amend rewrites rather than adds. Both of its failure modes are answerable from
    # local git state alone, so this costs nothing and runs first.
    if not hit and is_amend(cmd):
        acwd = os.getcwd()
        published = head_already_published(acwd)
        if published:
            hit = ("amend-published",
                   (f"HEAD is already contained in `{published}`, so `--amend` would not add a "
                   "commit — it would REWRITE the one HEAD points at, which is not yours. "
                   "This happened once already: the amend replaced the maintainer's commit "
                   "and the branch then proposed undoing their work, visible only as an "
                   "unrelated file showing up as `new` in the diff.\n\n"
                   "You almost certainly want a new commit:\n"
                   "    git commit -F <message-file>\n"
                   "and if you meant to amend your own work, check `git rev-parse HEAD^` "
                   "is the base you expect first."))
        else:
            n, tops = staged_summary(acwd)
            if n > AMEND_STAGED_LIMIT:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        f"[amend-sweep] This amend would carry {n} staged files: {tops}. "
                        "`--amend` commits whatever is in the index and prints one sha, so a "
                        "build's generated output sitting there gets folded in silently — "
                        "twice in one session that turned 7 files into 56, then 13 into 61. "
                        "Confirm the staged set is what you mean:\n"
                        "    git diff --cached --name-only"),
                }}))
                sys.exit(0)

    if hit:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"[{hit[0]}] {hit[1]}",
        }}))
