#!/usr/bin/env python3
"""Find tests that plant a fault without proving the fault landed.

A test suite's plants are code, and nobody tests them. They fail the way all setup fails —
silently, exit 0, with the assertion afterwards still producing a confident answer about a
fixture that does not contain the fault.

Two shapes, both shipped in this toolchain:

  nothing happened    `sed -i 's/^## Checklist$/## Checklis/'` where the anchor moved. The
                      fixture stays clean, the check correctly stays quiet, and the case
                      reports the CHECKER as broken. Hours went into the wrong file.

  too much happened   `open(p,"wb").write(open(p,"rb").read().replace(...))`. Python
                      evaluates the `open(...,"wb")` first, truncating the file, so the read
                      returns nothing and the fixture is emptied. A byte-comparison guard
                      does NOT catch this — the file did change.

So this looks for ANCHORED IN-PLACE MUTATION of a fixture in a test file, and asks whether
anything in that file ever verifies a mutation applied.

Deliberately narrow. It does NOT flag:
  - `printf ... > fixture`   whole-file write with literal content: no anchor to miss
  - `printf ... >> fixture`  append: cannot silently no-op
  - mutation in a non-test file: a build script rewriting a config is not a plant

    test-guard.py <path> [path ...]      files or directories
    The corpus lives in `ops/devlane/hooks/tests/`.

Exit 1 if any file plants without a guard.
"""

import re
import sys
from pathlib import Path

# a file is a test if it says so in its name
# Files that are read, not run. See the PROSE note in scan().
PROSE = {".md", ".rst", ".txt", ".adoc"}

TESTish = re.compile(r"(^|[-_./])(test|tests|spec|selftest|check-test)([-_.]|$)", re.IGNORECASE)

# ANCHORED in-place mutation: needs an existing string to match, so it can silently no-op
ANCHORED = [
    (re.compile(r"\bsed\s+(-[a-zA-Z]*i[a-zA-Z]*\s+)"), "sed -i (anchor may not match)"),
    (re.compile(r"\bperl\s+-[a-zA-Z]*p[a-zA-Z]*i"), "perl -pi (anchor may not match)"),
    (re.compile(r"\.replace\([\s\S]{0,120}?\bwrite_(text|bytes)\b"), "read/replace/write-back"),
    (re.compile(r"\bwrite_(text|bytes)\([\s\S]{0,120}?\.replace\("), "read/replace/write-back"),
    (re.compile(r"\bsub\(\s*[^)]*\)[\s\S]{0,80}?\bwrite_(text|bytes)\b"), "re.sub write-back"),
]

# Guards that prove a mutation landed. These are COMPARISONS ONLY.
#
# An earlier version accepted `plant()` — the name of the helper — as evidence. Removing
# every real comparison from the harness then left it "guarded", because the function was
# still called plant. A name is not a check; that is the same proxy-for-the-thing mistake
# this tool exists to catch, committed inside the tool.
GUARD = re.compile(
    r"\bcksum\b|\bmd5sum\b|\bsha1sum\b|\bsha256sum\b|\bcmp\s|\bdiff\s|\bwc\s+-c\b|"
    r"assert.*chang|before\s*!=\s*after|after\s*!=\s*before|"
    r"\bst_size\b|\bgetsize\b|\bstat\(\)\.st_|\bassert .*\bin\b.*read",
    re.IGNORECASE)

# always a bug, in any file: the write truncates before the read runs
TRUNCATING = re.compile(
    r"open\(\s*([A-Za-z_][\w.\[\]\"']*)\s*,\s*[\"']w[b+]*[\"']\s*\)"
    r"\s*\.\s*write\(\s*open\(\s*\1\b")


ALLOW = re.compile(r"test-guard:\s*allow")
TRIPLE = re.compile('"""[\\s\\S]*?"""' + "|'''[\\s\\S]*?'''")


def strip_noise(text, is_python):
    """Prose that describes these patterns does not execute them.

    Comment lines are blanked everywhere. Triple-quoted regions are blanked only in Python,
    where they are inert data — a shell heredoc looks similar and RUNS, so it is kept. Lines
    carrying `test-guard: allow` are deliberate fixtures: a harness that tests its own guard
    has to contain the bug it guards against.
    """
    if is_python:
        text = TRIPLE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    out = []
    for line in text.splitlines():
        st = line.lstrip()
        out.append("" if st.startswith(("#", "//")) or ALLOW.search(line) else line)
    return "\n".join(out)


def scan(path):
    """-> list of (line_no, kind, detail) findings for one file."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    code = strip_noise(raw, Path(path).suffix == ".py")
    found = []

    # The truncating self-read is a bug in anything that RUNS. In prose it is usually the
    # opposite — someone documenting the trap so it can be recognised — and flagging that
    # fires on every edit to the document defining the rule, which is how a guard becomes
    # noise and then gets ignored. Prose is not executed; if the snippet ever moves into a
    # script, it is caught there.
    for m in (() if Path(path).suffix.lower() in PROSE else TRUNCATING.finditer(code)):
        found.append((code[:m.start()].count("\n") + 1, "truncating-self-read",
                      ("the write truncates the file before the read executes — the fixture "
                      "ends up EMPTY. Read into a variable first.")))

    if not TESTish.search(str(path)):
        return found

    guarded = bool(GUARD.search(code))
    if guarded:
        return found

    for pat, why in ANCHORED:
        m = pat.search(code)
        if m:
            found.append((code[:m.start()].count("\n") + 1, "unguarded-plant",
                          f"{why}, and nothing in this file checks a mutation landed"))
            break                       # one finding per file: this is a per-file property
    return found


def main(argv):
    paths = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            paths += [q for q in p.rglob("*") if q.is_file() and q.suffix in
                      (".sh", ".bash", ".py", ".zsh", "")]
        elif p.is_file():
            paths.append(p)
    if not paths:
        print("usage: test-guard.py <path> [path ...]", file=sys.stderr)
        return 2

    bad = []
    for p in sorted(set(paths)):
        for line, kind, detail in scan(p):
            bad.append((p, line, kind, detail))

    if not bad:
        print(f"  every plant is guarded ({len(paths)} file(s) scanned)")
        return 0

    print("  tests that plant a fault without proving it landed:\n")
    for p, line, kind, detail in bad:
        print(f"    {p}:{line}  [{kind}]")
        print(f"      {detail}")
    print(f"\n  {len(bad)} finding(s). A plant that silently no-ops makes its case "
          f"uninterpretable:\n  the check then runs against a clean fixture and answers "
          f"a question nobody asked.\n  Fix: compare the fixture before and after, and fail "
          f"the case if it did not change.\n")
    return 1


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        print("corpus moved to ops/devlane/hooks/tests/", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
