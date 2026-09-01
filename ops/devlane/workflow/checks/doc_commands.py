#!/usr/bin/env python3
"""A document that tells you to run something must name something that
is there.

`ghost_link_errors` already refuses a markdown link to a missing `.md`.
It cannot see a command: `./lessons.sh` inside an ```sh fence is not a
link, so a shelf document instructed a reader to run a script that
existed nowhere in the repository and everything reported success --
the doc rendered, the docs-index suite passed, the ghost-link check
passed, CI was green. Two reviewers caught it by reading the prose.

Exit 0 when every command in every shell fence resolves, 1 when one
does not, and print each miss with its file and line so the reader does
not have to go and find it.

    doc_commands.py [--root DIR] [PATH ...]

Fences are scanned line by line rather than by pairing ``` to the next
```. A regex doing the latter can begin at a CLOSING fence and capture
the prose after it as if it were shell -- which made the first version
of this scanner return zero hits on the very document that carried the
fault. The state machine is not fastidiousness; it is the difference
between a check and a decoration.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: Only fences that claim to be shell. An unlabelled fence is usually
#: output or a transcript, and assuming otherwise is how a checker earns
#: its reputation for noise.
SHELL_LANGS = {"sh", "bash", "console", "shell"}

#: The command word: the first token of a line, optionally preceded by an
#: interpreter. A path that appears later on the line is an argument --
#: `git add ops/devlane/gone.py` is talking about a file, not running one.
COMMAND = re.compile(
    r"""^\s*
        (?:(?:python3?|bash|sh)\s+)?      # an interpreter, if any
        (?P<path>(?:\./|[\w.-]+/)[\w./-]+\.(?:sh|py))
        (?=\s|$)                          # the whole token, not a prefix
    """,
    re.VERBOSE,
)


class DocumentReadError(Exception):
    """The document was selected for scanning but could not be read."""

    def __init__(self, path, error):
        self.path = path
        self.error = error
        super().__init__(str(error))


def shell_fences(text):
    """(line_number, line) for every line inside a shell fence.

    Line-scanned, so an opening fence is only ever a line that opens one.
    """
    out, lang = [], None
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            lang = None if lang is not None else (
                stripped[3:].strip().lower() or "-")
            continue
        if lang in SHELL_LANGS:
            out.append((n, line))
    return out


def command_misses(path: Path, root: Path):
    """Commands in this document's shell fences that resolve to nothing."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # Survive unreadable input, but do not turn an unperformed scan into
        # a clean result. The caller reports every unreadable document.
        raise DocumentReadError(path, err) from err
    misses = []
    for n, line in shell_fences(text):
        m = COMMAND.match(line)
        if not m:
            continue
        raw = m.group("path")
        rel = raw.removeprefix("./")
        if (path.parent / rel).exists() or (root / rel).exists():
            continue
        misses.append((n, raw))
    return misses


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args(argv)
    try:
        root = Path(a.root).resolve()
    except (OSError, RuntimeError) as err:
        print(f"{a.root}: doc-commands: INVALID — root could not be"
              f" resolved ({err})")
        return 1
    if not root.is_dir():
        print(f"{root}: doc-commands: INVALID — root is not a directory")
        return 1
    resolution_errors = []
    if a.paths:
        docs = []
        for raw in a.paths:
            path = Path(raw)
            try:
                docs.append(path.resolve())
            except (OSError, RuntimeError) as err:
                resolution_errors.append(DocumentReadError(path, err))
    else:
        docs = sorted(p for p in root.rglob("*.md")
                      if ".git" not in p.parts)
    hits = 0
    unreadable = 0
    for err in resolution_errors:
        print(f"{err.path}: doc-commands: INVALID — could not be read"
              f" ({err.error})")
        unreadable += 1
    for d in docs:
        try:
            misses = command_misses(d, root)
        except DocumentReadError as err:
            try:
                shown = d.relative_to(root)
            except ValueError:
                shown = d
            print(f"{shown}: doc-commands: INVALID — could not be read"
                  f" ({err.error})")
            unreadable += 1
            continue
        for n, raw in misses:
            try:
                shown = d.relative_to(root)
            except ValueError:
                shown = d
            print(f"{shown}:{n}: `{raw}` is run here and does not exist")
            hits += 1
    if hits or unreadable:
        if unreadable:
            print(f"doc-commands: INVALID — {unreadable} document(s)"
                  " could not be scanned")
        if not hits:
            return 1
        print(f"doc-commands: {hits} command(s) name nothing")
        return 1
    print(f"doc-commands: clean — {len(docs)} document(s) scanned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
