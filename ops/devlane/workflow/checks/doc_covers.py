#!/usr/bin/env python3
"""Check shelf-document frontmatter and the paths named by ``covers:``.

Three classes of bad input, three different obligations, and one exit
code between them — so the printed line is the only thing that tells
them apart, and the summary never says clean when any of them fired.

    doc_covers.py [--root DIR] [PATH ...]

A **finding** is a declaration that is wrong: a path that resolves to
nothing, a path that resolves somewhere it should not, a `covers:`
value that is not a list of paths, a header that never closes or lacks
`name:`/`description:`. **INVALID** is a subject that could not be
judged at all: a document that cannot be read or decoded, a path whose
resolution raises, a `--root` that is not a directory, a shelf that is
not there, a shelf with no `.md` in it. "Nobody looked" is not "clean"
— the lint and imports checkers learned that first, and doc_commands
gave INVALID its printed shape.

`covers:` names repo-relative paths. Absolute and `..` are refused
LEXICALLY, before any filesystem call: a checker that stats an absolute
path has already reached outside the repository it is judging, and
`.dev/docs/../docs/README.md` is not a name, it is a route. The
inside test resolves both sides, because a `/tmp` that is itself a
symlink (macOS) would otherwise report every inside path as outside.

Exit 0 or 1, never 2, 3, 64 or 70 — those are `wf`'s, so argparse's
own exit is intercepted rather than inherited.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

#: The shelf, relative to the repository root.
SHELF = (".dev", "docs")

FINDING = "finding"
INVALID = "invalid"


class Problem(NamedTuple):
    """One printed line, and which count it belongs to."""

    kind: str
    message: str


class _Usage(Exception):
    """argparse asked to exit. Carries the code this checker may use."""

    def __init__(self, status, message=""):
        self.status = status
        self.message = message
        super().__init__(message)


class _Parser(argparse.ArgumentParser):
    """Stock argparse leaves on 2 for an unknown flag, which is `wf`'s
    code for a usage error and would arrive in CI as neither pass nor
    fail. Both exits are raised instead and mapped by main()."""

    def error(self, message):
        raise _Usage(1, message)

    def exit(self, status=0, message=None):
        raise _Usage(1 if status else 0, message or "")


def header_block(text):
    """(block, opened, closed) — the ONE place a document's `---`
    header is split. Two parsers of one header is the drift this
    checker exists to hold together."""
    if not text.startswith("---\n"):
        return "", False, False
    parts = text.split("---", 2)
    if len(parts) != 3:
        return "", True, False
    return parts[1], True, True


def covers_declarations(text):
    """(raw paths, malformed messages) for EVERY `covers:` key.

    Every key is read and every item is judged: the parser this
    replaced stopped at the first key and dropped a scalar value or an
    empty `- ` item on the floor, so a declaration that named nothing
    real reported clean. The two branches judge the same way — an item
    is empty once its quotes are off, and a value that is a mapping is
    as much "not a list" as a scalar is.
    """
    block, opened, closed = header_block(text)
    if not (opened and closed):
        return [], []
    paths, malformed = [], []
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("covers:"):
            continue
        rest = line.split(":", 1)[1].strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            # `[]` is a list with no items; `""` is an item that is
            # empty. The quotes come off BEFORE the test, or the empty
            # item is judged as a path and resolves to the repo root.
            items = inner.split(",") if inner else []
            for item in items:
                value = item.strip().strip("'\"")
                if value:
                    paths.append(value)
                else:
                    malformed.append("covers: has an empty list item")
            continue
        if rest:
            malformed.append(
                f"covers: {rest} is a scalar; covers: names a list of paths")
            continue
        for follow in lines[index + 1:]:
            stripped = follow.strip()
            # Column 0 ends the block — the next key, and in particular
            # a second `covers:` that is its own declaration, never a
            # value of this one. A blank line ends it too, and is not a
            # finding.
            if not stripped or not follow[:1].isspace():
                break
            if stripped != "-" and not stripped.startswith("- "):
                malformed.append(f"covers: {stripped} is not a list item;"
                                 " covers: names a list of paths")
                break
            item = stripped[1:].strip().strip("'\"")
            if item:
                paths.append(item)
            else:
                malformed.append("covers: has an empty list item")
    return paths, malformed


def covers_paths(text):
    """Paths named by a `covers:` key in YAML-ish frontmatter."""
    return covers_declarations(text)[0]


def covers_path_problem(raw, name, repo):
    """One `covers:` path, judged. None when it is a real repo path."""
    lexical = Path(raw)
    if lexical.is_absolute():
        return Problem(FINDING, f"{name}: covers: {raw} is absolute;"
                                " covers: names repo-relative paths")
    if ".." in lexical.parts:
        return Problem(FINDING, f"{name}: covers: {raw} has a .. segment;"
                                " covers: names a path, not a route")
    target = repo / raw
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError) as err:
        return Problem(INVALID, f"{name}: doc-covers: INVALID — covers:"
                                f" {raw} could not be resolved ({err})")
    try:
        target.stat()
    except FileNotFoundError:
        return Problem(FINDING, f"{name}: covers: {raw} does not exist")
    except (OSError, ValueError) as err:
        return Problem(INVALID, f"{name}: doc-covers: INVALID — covers:"
                                f" {raw} could not be read ({err})")
    if not resolved.is_relative_to(repo):
        return Problem(FINDING, f"{name}: covers: {raw} resolves outside"
                                " the repository")
    return None


def covers_problems(text, name, repo):
    """(problems, paths judged) for one document's `covers:` block."""
    root = Path(repo).resolve()
    paths, malformed = covers_declarations(text)
    problems = [Problem(FINDING, f"{name}: {message}")
                for message in malformed]
    for raw in paths:
        problem = covers_path_problem(raw, name, root)
        if problem is not None:
            problems.append(problem)
    return problems, len(paths)


def covers_path_errors(text, name, repo):
    """Every covers: path must be a real repo-relative path in the tree."""
    return [problem.message for problem in covers_problems(text, name, repo)[0]]


def frontmatter_errors(text, name):
    """The production frontmatter check — driven by the class test on
    every live doc AND by the self-tests on planted shapes, so a
    reverted branch cannot stay green (Grok, PR #30 round three)."""
    block, opened, closed = header_block(text)
    if not opened:
        return [f"{name}: has no frontmatter"]
    if not closed:
        return [f"{name}: frontmatter never closes"]
    return [f"{name}: frontmatter lacks a {key}: line"
            for key in ("name", "description")
            if not re.search(rf"(?m)^{key}:", block)]


def shown_as(doc, repo):
    """The document's name as a reader of the repository knows it."""
    try:
        return str(doc.relative_to(repo))
    except ValueError:
        return str(doc)


def judge(docs, repo):
    """(problems, documents scanned, covers: paths judged)."""
    problems, paths = [], 0
    for doc in docs:
        name = shown_as(doc, repo)
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            problems.append(Problem(
                INVALID,
                f"{name}: doc-covers: INVALID — could not be read ({err})"))
            continue
        if not text.startswith("---\n"):
            # BOUNDARY: the checker judges the header of a document that
            # has one. TODO.md and the shelf index carry prose only.
            continue
        problems.extend(Problem(FINDING, message)
                        for message in frontmatter_errors(text, name))
        found, counted = covers_problems(text, name, repo)
        problems.extend(found)
        paths += counted
    return problems, len(docs), paths


def main(argv=None):
    ap = _Parser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("paths", nargs="*")
    try:
        args = ap.parse_args(argv)
    except _Usage as usage:
        if usage.status:
            print(f"doc-covers: INVALID — {usage.message.strip()}")
        return usage.status

    try:
        repo = Path(args.root).resolve(strict=True)
    except (OSError, RuntimeError) as err:
        print(f"{args.root}: doc-covers: INVALID — root could not be"
              f" resolved ({err})")
        return 1
    if not repo.is_dir():
        print(f"{args.root}: doc-covers: INVALID — root is not a directory")
        return 1

    shelf_name = str(Path(*SHELF))
    if args.paths:
        docs = [Path(raw) for raw in args.paths]
    else:
        shelf = repo.joinpath(*SHELF)
        if not shelf.is_dir():
            print(f"{shelf_name}: doc-covers: INVALID — the shelf is not"
                  " a directory here")
            return 1
        docs = sorted(shelf.rglob("*.md"))
        if not docs:
            print(f"{shelf_name}: doc-covers: INVALID — no .md document"
                  " under the shelf to judge")
            return 1

    problems, scanned, paths = judge(docs, repo)
    for problem in problems:
        print(problem.message)
    invalid = sum(1 for problem in problems if problem.kind == INVALID)
    findings = len(problems) - invalid
    if invalid:
        print(f"doc-covers: INVALID — {invalid} subject(s) could not be"
              " judged")
    if findings:
        print(f"doc-covers: {findings} finding(s)")
    if problems:
        return 1
    print(f"doc-covers: clean — {scanned} doc(s), {paths} covers: path(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
