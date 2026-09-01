#!/usr/bin/env python3
"""The term wall: names this organisation does not use, refused everywhere.

Some names must not appear in this organisation's trees, commit
messages, or pull-request text — not affirmed, not negated, not cited.
This check is the wall. The pattern is configuration, never tree
content: it is read from the environment variable `TERM_WALL`, falling
back to the gitignored file `<root>/ops/bin/term-wall.conf` (one line:
an extended, case-insensitive regular expression). No pattern is a
refusal, never a vacuous pass. Every hit this check prints is masked,
so its output does not carry what the tree may not.

    term_wall.py [--root DIR] [PATH ...]     tracked files (default: all)
    term_wall.py --message-file FILE         one commit message
    term_wall.py --range BASE..HEAD          every commit message in the range
    term_wall.py --stdin                     text on stdin

Exit 0 clean; 1 on a hit, every hit printed; 2 on a refusal, one line
on stderr in the lane's shape: `class: expected …; found …; needed …`.
The same wall stands in CI (the org's term-wall action) — this copy is
the local hook's and the landing lever's. Contract:
CONTRACT-term-wall.md beside this file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
import re

MASK = "[forbidden name]"
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".db", ".phar"}


def refuse(cls: str, expected: str, found: str, needed: str) -> None:
    print(f"{cls}: expected {expected}; found {found}; needed {needed}", file=sys.stderr)
    sys.exit(2)


def load_wall(root: Path) -> re.Pattern[str]:
    pattern = os.environ.get("TERM_WALL", "")
    if not pattern:
        conf = root / "ops" / "bin" / "term-wall.conf"
        if conf.is_file():
            text = conf.read_text(encoding="utf-8", errors="replace").strip()
            pattern = text.splitlines()[0].strip() if text else ""
    if not pattern:
        refuse("pattern", "TERM_WALL set", "empty",
               "the org variable (CI) or ops/bin/term-wall.conf (local)")
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        refuse("pattern", "a compilable regular expression", f"({exc})",
               "a valid extended regex in TERM_WALL or ops/bin/term-wall.conf")
    raise AssertionError("unreachable")


def scan_text(wall: re.Pattern[str], text: str, where: str) -> list[str]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if wall.search(line):
            hits.append(f"{where}:{n}: {wall.sub(MASK, line).strip()[:160]}")
    return hits


def tracked(root: Path, paths: list[str]) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", *paths],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        refuse("root", "a git work tree", f"{root} ({exc})", "run inside a clone or pass --root")
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def scan_tree(wall: re.Pattern[str], root: Path, paths: list[str]) -> list[str]:
    hits = []
    for rel in tracked(root, paths):
        if wall.search(rel):
            hits.append(f"{rel}: forbidden name in the path ({wall.sub(MASK, rel)})")
        p = root / rel
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" in data[:8000]:
            continue
        hits.extend(scan_text(wall, data.decode("utf-8", "replace"), rel))
    return hits


def scan_range(wall: re.Pattern[str], root: Path, rng: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%H%x00%B%x00", rng],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        refuse("range", "BASE..HEAD git can resolve",
               f"{rng} ({detail.decode('utf-8', 'replace').strip() or exc})",
               "a range of reachable commits")
    hits = []
    parts = out.decode("utf-8", "replace").split("\0")
    for sha, body in zip(parts[0::2], parts[1::2]):
        sha = sha.strip()
        if sha:
            hits.extend(scan_text(wall, body, f"commit {sha[:12]}"))
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--message-file", help="one commit message, as the commit-msg hook is given it")
    mode.add_argument("--range", dest="rng", help="BASE..HEAD — every commit message in the range")
    mode.add_argument("--stdin", action="store_true", help="scan text on stdin")
    ap.add_argument("--root", default=".", help="the work tree (default: current directory)")
    ap.add_argument("paths", nargs="*", help="tracked paths to scan (default: every tracked file)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    wall = load_wall(root)

    if args.message_file:
        path = Path(args.message_file)
        if not path.is_file():
            refuse("message", "a readable message file", str(path), "the path git hands the commit-msg hook")
        hits = scan_text(wall, path.read_text(encoding="utf-8", errors="replace"), "message")
    elif args.rng:
        hits = scan_range(wall, root, args.rng)
    elif args.stdin:
        hits = scan_text(wall, sys.stdin.read(), "stdin")
    else:
        if not root.is_dir():
            refuse("root", "a directory", str(root), "an existing work tree")
        hits = scan_tree(wall, root, args.paths)

    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
