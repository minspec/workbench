#!/usr/bin/env python3
"""A day-one secret scan (PLAN D8's seed, as data-driven as anything else).

Deliberately small and high-signal. It looks for credential shapes that are
unambiguous — a private key header, an AWS key id, a GitHub token prefix, a
Slack token — rather than trying to be a general scanner. A scanner that
cries wolf gets disabled, and a disabled check is worse than none.

D8 is still open in PLAN §12: this seeds the `security` class so a security
stage has something real to require, and it is an argv template in the
gate-kind spec, so replacing it with a heavier scanner is a data change.

    secret_scan.py [--root DIR] [PATH ...]

Exit 0 clean, 1 on a hit. Tracked files only, so an untracked scratch file
does not fail the gate.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SIGNATURES = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abpsr]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("PEM certificate key", re.compile(r"-----BEGIN ENCRYPTED PRIVATE KEY-----")),
]

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".db"}

#: A line carrying this marker is skipped. The suite that proves this scanner
#: fires has to contain credential-shaped strings, and so will any fixture or
#: documentation example. Marking the line is deliberate and greppable, which
#: is the same idiom test-guard uses; a blanket "skip tests" rule would hide
#: a real key committed to a test file.
ALLOW_MARKER = "secret-scan: allow"


def tracked_files(root: Path, explicit):
    if explicit:
        # the documented [PATH ...] form accepts directories: expand
        # each to the tracked files beneath it — silently dropping a
        # directory made `secret_scan.py .` a scan of nothing
        # (Grok, PR #32 review)
        chosen = []
        for raw in explicit:
            path = Path(raw)
            if path.is_dir():
                out = subprocess.run(
                    ["git", "-C", str(root), "ls-files", "-z", "--", raw],
                    capture_output=True, text=True, check=False)
                if out.returncode == 0:
                    chosen.extend(root / q for q in out.stdout.split("\0")
                                  if q)
            elif path.is_file():
                chosen.append(path)
        return chosen
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return [p for p in sorted(root.rglob("*")) if p.is_file()]
    return [root / p for p in out.stdout.split("\0") if p]


def main() -> int:
    parser = argparse.ArgumentParser(description="day-one secret scan")
    parser.add_argument("--root", default=".")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    hits, scanned, allowed = [], 0, 0
    # This file necessarily contains the patterns it looks for.
    myself = Path(__file__).resolve()

    for path in tracked_files(root, args.paths):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.resolve() == myself:
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                allowed += 1
                continue
            for label, pattern in SIGNATURES:
                if pattern.search(line):
                    try:
                        shown = path.relative_to(root).as_posix()
                    except ValueError:
                        shown = str(path)
                    hits.append(f"{shown}:{number}: {label}")

    if hits:
        print(f"secret scan: {len(hits)} candidate credential(s)")
        for hit in hits:
            print(f"  {hit}")
        return 1
    if scanned == 0:
        # a scan that read nothing proved nothing: reporting it clean
        # converts "nobody looked" into "we checked and it was fine"
        print("secret scan: INVALID — the fileset matched zero"
              " tracked files; a scan over nothing is not a clean scan")
        return 1
    print(f"secret scan: clean — {scanned} tracked file(s),"
          f" {len(SIGNATURES)} signature(s), {allowed} allowlisted line(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
