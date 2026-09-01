#!/usr/bin/env python3
"""A trailer that git will not parse is not a trailer.

The failure, measured on 2026-08-24 across `dev`, `task/runner-slice-1`
and `contracts/lift-and-enforce`: of 181 commits carrying a `Source:`
line, **50 of them** are invisible to
`git log --format='%(trailers:key=Source)'`. Nineteen on 08-20,
twenty-seven on 08-21, four on 08-24. The commit titled "apply Codex's
six P1 findings on attribution" is itself one of them.

The cause is always the same and never visible: git's trailer block is
the LAST paragraph of a message, so a blank line between two trailers
demotes everything above it to body text.

    Source: original          <- body text now
                              <- this blank line is the bug
    Co-Authored-By: A <a@x>   <- git parses only this

`git commit` exits 0, `git log` prints the line, review sees a trailer
sitting exactly where trailers go. Only a machine query reveals the
break, and the whole point of a trailer is that a machine can query it.
So this must be checked mechanically or not at all.

**git is the oracle, never a parser written here.** The rules for what
counts as a trailer are git's, they are subtle, and a second
implementation would drift from the first the day either changed. This
compares a raw scan of the message against
`git interpret-trailers --parse` and reports where they disagree.

Refusals, not silences: the check is INVALID -- exit 1, never 0 -- when
git cannot be run, when a range names no commit, or when a message file
is missing or empty. "Nobody looked" must never render as "we checked
and it was fine".

    commit_trailers.py --message-file FILE     # commit-msg hook
    commit_trailers.py --range BASE..HEAD      # registered check
    commit_trailers.py --range BASE..HEAD --json

Exit 0 clean, 1 on a violation or an INVALID run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

#: A line shaped like a trailer: `Key: value`, key at column zero.
TRAILER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*): +(\S.*)$")

#: The keys this repo means as trailers, as data. Shape alone is far too
#: wide to check on: this repo's commit subjects are `workflow: ...`,
#: `docs: ...`, `telemetry: ...`, and a body that quotes one is a line
#: indistinguishable from a trailer. Checking shape flagged 187 of 243
#: commits, of which 137 were subject prefixes -- a checker firing on
#: three quarters of history is one nobody reads.
#:
#: The cost of a list is that a NEW key is unchecked until it is added
#: here. That is the right trade only because the list is data and the
#: suite pins it: adding a key is one line, and a false positive rate
#: this high is not recoverable.
# AGENTS.md SS1: a specific model name, then a vendor noreply address.
# Deliberately loose on the name -- new models appear and a checker that
# enumerates them refuses tomorrow's -- and strict on the shape, which is
# what makes the trailer queryable at all.
ATTRIBUTION = re.compile(r"^.*\S.* <[^@<>\s]+@[^@<>\s]+>$")

KNOWN_KEYS = frozenset({
    "Source",             # CONTRIB.md, required on every commit
    "Co-Authored-By",     # AGENTS.md attribution
    "Reviewed-by",        # CONTRIB.md fix-commit template
    "Claude-Session",
    "Signed-off-by",
    "WO", "Stage", "Event",   # wf auto-commits, per commitmsg.cue
})


class Invalid(Exception):
    """The check could not run. Never reported as a pass."""


def parsed_pairs(message: str) -> set[tuple[str, str]]:
    """The (key, value) trailers git ACTUALLY parses. git is the oracle.

    Pairs rather than keys, because a key alone loses an occurrence: an
    orphaned `Source: lost-source` followed by a valid `Source: original`
    put `Source` in the set and suppressed the orphan, so a declared
    origin git cannot see reported clean (Codex, PR #51).
    """
    try:
        proc = subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            input=message, capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise Invalid(f"git could not be run ({exc})") from exc
    if proc.returncode != 0:
        raise Invalid(
            f"git interpret-trailers failed: {proc.stderr.strip()}")
    pairs = set()
    for line in proc.stdout.splitlines():
        match = TRAILER_LINE.match(line)
        if match:
            pairs.add((match.group(1), match.group(2).strip()))
    return pairs


def orphaned(message: str) -> list[dict]:
    """Trailer-shaped lines git does not parse, in blocks that claim to be trailers.

    The precision hinge. A message may legitimately DISCUSS a trailer --
    this file's own commit does, and so does every commit that quotes
    `Source: original` while explaining it. Such a mention is prose
    inside a sentence, or indented inside a fence; it is never a whole
    paragraph made of nothing but trailer-shaped lines.

    So a key is reported only when all three hold: it is shaped like a
    trailer at column zero, git's parse does NOT contain it, and its
    paragraph is entirely trailer-shaped lines. That last condition is
    what separates a real orphaned trailer block from prose about one.
    """
    lines = message.splitlines()

    paragraphs, current, start = [], [], 0
    for index, line in enumerate(lines):
        if line.strip():
            if not current:
                start = index
            current.append(line)
        elif current:
            paragraphs.append((start, current))
            current = []
    if current:
        paragraphs.append((start, current))
    if not paragraphs:
        return []

    last = paragraphs[-1][1]
    found = []
    for start_line, block in paragraphs:
        if block is last:
            continue
        if not all(TRAILER_LINE.match(line) for line in block):
            continue          # prose, or a mixed paragraph: not a claim
        for offset, line in enumerate(block):
            key = TRAILER_LINE.match(line).group(1)
            # No membership test. git parses the LAST paragraph and no
            # other, so a known-key line in an earlier all-trailer block
            # is unparsed by construction -- checking whether the same
            # pair ALSO appears in the final block only suppressed the
            # report when someone wrote the trailer twice, which is the
            # case where a declared origin is silently lost (Codex,
            # PR #51). Membership could only ever hide a true positive.
            if key in KNOWN_KEYS:
                found.append({
                    "key": key,
                    "line": start_line + offset + 1,
                    "text": line.strip(),
                })
    return found


def is_merge(sha: str) -> bool:
    """Is this a real merge? Asked of git, never of the subject line.

    Merges carry no Source -- every merge on dev is a generated "Merge
    pull request #N" with no trailers at all -- so they must be exempt
    or every PR merge fails. The first version read that exemption off
    the SUBJECT, which made the mandatory attribution bypassable by
    titling any commit `Merge branch ...` (Codex, PR #51). Parentage is
    a fact; a subject is a claim.

    Takes a sha, because parentage is only a fact once the commit
    exists. See check_message on why the hook does not ask.
    """
    proc = subprocess.run(["git", "rev-list", "--parents", "-n", "1", sha],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return False
    return len(proc.stdout.split()) > 2


def check_message(message: str, label: str, *, require_source: bool = True,
                  sha: str | None = None) -> list[str]:
    if not message.strip():
        raise Invalid(f"{label}: the message is empty")
    problems = []
    # A trailer that is ABSENT is as unqueryable as one that is malformed,
    # and CONTRIB.md §Naming requires Source on every commit. The check
    # began by comparing a raw scan against git's parse, which by
    # construction can only see trailers that are THERE -- so a message
    # with no Source at all passed clean (Codex, PR #51).
    #
    # Only over a RANGE, where `sha` names an existing commit. At hook
    # time the commit does not exist, so its parentage is not a fact yet:
    # MERGE_HEAD covers a merge in progress but not `commit --amend` on a
    # merge, where MERGE_HEAD is already gone and the result still keeps
    # both parents -- the hook refused a legitimate amend that CI exempts
    # (Codex, PR #51 round four). Rather than guess parentage from the
    # message or the reflog, the hook does not enforce this at all: it is
    # local convenience, and WF:gates is the gate that cannot be dodged.
    # The hook still catches the orphaned trailer, which needs only the
    # message.
    if (require_source and sha is not None and not is_merge(sha)
            and not any(k == "Source" for k, _ in parsed_pairs(message))):
        problems.append(
            f"{label}: no `Source:` trailer git can parse — CONTRIB.md "
            f"requires one on every non-merge commit"
        )
    # Same requirement, same reason, a different trailer. AGENTS.md SS1
    # states it flatly: "Every commit carries a `Co-Authored-By` trailer
    # naming the specific agent -- model, not brand; vendor noreply
    # address." An absent one was accepted, and so was
    # `Co-Authored-By: arbitrary`, because only parseability was asked
    # (Codex, PR #51 round five). Parseability is not the requirement;
    # the documented form is, so the VALUE is checked too.
    #
    # Range mode only, for the parentage reason above.
    if require_source and sha is not None and not is_merge(sha):
        coauthors = [v for k, v in parsed_pairs(message) if k == "Co-Authored-By"]
        if not coauthors:
            problems.append(
                f"{label}: no `Co-Authored-By:` trailer git can parse — "
                f"AGENTS.md requires one naming the specific agent on "
                f"every non-merge commit"
            )
        elif not any(ATTRIBUTION.match(v) for v in coauthors):
            problems.append(
                f"{label}: `Co-Authored-By: {coauthors[0]}` is not the "
                f"documented form — AGENTS.md requires a specific model "
                f"name and a vendor address, e.g. "
                f"`Claude Fable 5 <noreply@anthropic.com>`"
            )
    return problems + [
        f"{label}: `{item['text']}` at line {item['line']} is not a "
        f"trailer git will parse — a blank line above the final block "
        f"demotes it to body text"
        for item in orphaned(message)
    ]


def commits_in(rng: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-list", rng], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise Invalid(f"git could not be run ({exc})") from exc
    if proc.returncode != 0:
        raise Invalid(f"{rng} is not a range git can list: {proc.stderr.strip()}")
    shas = proc.stdout.split()
    if not shas:
        raise Invalid(f"{rng} names no commit, so nothing was checked")
    return shas


def message_of(sha: str) -> str:
    proc = subprocess.run(["git", "log", "-1", "--format=%B", sha],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise Invalid(f"cannot read the message of {sha}")
    return proc.stdout


def range_from_event():
    """The commits THIS CI run is responsible for, from the event payload.

    Hard-coding `origin/dev` breaks on the branch it names: a `push` run
    for dev checks out the pushed commit and sets origin/dev to it, so
    `merge-base origin/dev HEAD` is HEAD, the range is empty, and the
    gate reports INVALID on every push to dev. A run for main would
    compare against an unrelated base instead (Codex, PR #51).

    GITHUB_EVENT_PATH is always set by Actions, so the range is read from
    the event rather than guessed: what a push added, or what a pull
    request proposes. Absent or unreadable, the caller falls back to the
    merge-base route, which is right for a developer's shell.
    """
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        return None
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    def here(sha):
        """Is this commit in the repository we are actually running in?

        GITHUB_EVENT_PATH is set for every step of a CI job, including
        steps that run the checker inside a throwaway fixture repo. That
        repo does not contain the event's commits, so using the event's
        range there asked about commits that do not exist and refused —
        which failed the gate-binding fixture's CLEAN arm in CI while
        passing locally, where the variable is unset (found by CI, not
        by me).
        """
        if not sha:
            return False
        return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True, check=False).returncode == 0

    pull = event.get("pull_request")
    if isinstance(pull, dict):
        base = (pull.get("base") or {}).get("sha")
        head = (pull.get("head") or {}).get("sha")
        if here(base) and here(head):
            return f"{base}..{head}"
        return None
    before, after = event.get("before"), event.get("after")
    # A new branch reports an all-zero `before`; there is no prior state
    # to diff against, so fall back rather than invent one.
    if before and set(before) != {"0"} and here(before) and here(after):
        return f"{before}..{after}"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--message-file", help="one message, as the commit-msg hook is given it")
    group.add_argument("--range", dest="rng", help="BASE..HEAD")
    group.add_argument("--since-base", action="store_true",
                       help="every commit this branch adds over --base-ref")
    ap.add_argument("--base-ref",
                    help="the ref --since-base measures from (default origin/dev)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    problems = []
    try:
        if args.message_file:
            path = Path(args.message_file)
            if not path.is_file():
                raise Invalid(f"{path} is not a file")
            problems = check_message(path.read_text(encoding="utf-8"), str(path))
            checked = 1
        else:
            rng = args.rng
            if args.since_base:
                # An explicitly supplied base is an operator instruction,
                # not a fallback.  Only consult Actions' event payload when
                # the caller left the base at its implicit default.
                event = range_from_event() if args.base_ref is None else None
                if event:
                    rng = event
                # Resolved here rather than baked into the registered argv:
                # a gate whose range is a literal string goes stale the day
                # the lane branch is renamed, and a shallow checkout makes it
                # silently empty. commits_in refuses an empty range, so a
                # clone without the base ref reports INVALID rather than a
                # pass over nothing.
            if args.since_base and not rng:
                base_ref = args.base_ref or "origin/dev"
                merge_base = subprocess.run(
                    ["git", "merge-base", base_ref, "HEAD"],
                    capture_output=True, text=True, check=False)
                if merge_base.returncode != 0:
                    raise Invalid(
                        f"{base_ref} is not available in this clone "
                        f"(a shallow checkout?): {merge_base.stderr.strip()}")
                rng = f"{merge_base.stdout.strip()}..HEAD"
            shas = commits_in(rng)
            checked = len(shas)
            for sha in shas:
                problems.extend(check_message(message_of(sha), sha[:7], sha=sha))
    except Invalid as exc:
        print(f"commit-trailers: INVALID — {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"checked": checked, "problems": problems},
                         indent=2, sort_keys=True))
    else:
        print(f"commit-trailers: checked {checked} message(s)")
        for problem in problems:
            print(f"  {problem}")
        if not problems:
            print("  clean — every trailer-shaped line is one git parses")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
