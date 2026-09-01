#!/usr/bin/env python3
"""The two-direction vocabulary wall, as a registered check (PLAN §10).

Dev-lane terms — work order, gate, stage, receipt, red/green, frozen set,
chain, claim, marker, packet, wf, capability — exist only under `.dev/`, in the root
dev-lane surfaces, and in dev-lane commits and PRs. They never appear in
the product's design artifacts, contracts or specs: the product describes itself
in its own vocabulary, and a process word in a design artifact is scope
leakage.

Exit 0 only when both passes are clean; exit 1 when either pass finds a term.
Prints every hit, because a check that says only "failed" makes the reader
go and re-run it by hand.

    vocabulary_wall.py [--root DIR] [PATH ...]

With no PATH, scans the design surfaces that exist: everything outside
`.dev/`, `.git/`, and the root dev-lane files that are allowed to use the
vocabulary.

The compose term is checked in the other direction across the dev-lane
surfaces. It is allowed only in `.dev/infra/` (compose),
`.dev/docs/scratch/` (research), `.dev/handoffs/` (systemd),
`.dev/records/` (evidence), `.dev/conductor/`
(conductor records quote evidence), and `.git/` (not a tree surface).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: Word-boundary patterns. `wf` is matched only as a bare word so that
#: ordinary English containing the letters is not a false positive.
RESERVED = [
    "work order", "work orders", "gate", "gates", "stage", "stages",
    "receipt", "receipts", "red stage", "green stage", "frozen set",
    "chain", "claim", "claims", "marker", "markers", "packet", "packets",
    "wf", "capability", "capabilities",
]

#: Surfaces where the vocabulary is allowed to appear. The class, not the
#: instances: root dot-directories holding tool and harness configuration
#: (.claude/ adapters, .serena/ code-assist config and memories) are
#: dev-lane surface — they describe how the repo is worked on, not what
#: the product is. Design artifacts never live in a dot-directory here.
ALLOWED_PREFIXES = (".dev/", ".git/", ".github/", ".claude/", ".serena/")
#: `ruff.toml` joins them for the same reason as the dot-directories:
#: it configures how this repo is worked on, and it cannot do that
#: without naming dev-lane paths — a per-file rule for
#: `.dev/app/workflow/wf.py` contains a reserved word in the PATH.
ALLOWED_FILES = ("AGENTS.md", "CONTRIB.md", "README.md", "ruff.toml")

TEXT_SUFFIXES = {".md", ".txt", ".rst", ".adoc", ".cue", ".toml", ".yaml", ".yml"}
CODE_SUFFIXES = (".py", ".json")
SERVICE_TERMS = ("serv\u0069ce", "serv\u0069ces")
SERVICE_RULE = "vocab.service-outside-infra"
PROD_TERMS = ("ker\u006eel",)
PROD_RULE = "vocab.kernel-outside-homes"
SERVICE_HOMES = (
    ".dev/infra/",
    ".dev/docs/scratch/",
    ".dev/handoffs/",
    ".dev/records/",
    ".dev/conductor/",
    ".git/",
)


def patterns_for(terms):
    return [(term, re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])", re.IGNORECASE))
            for term in terms]


def compound_service_patterns():
    """Return code/name patterns beyond the ordinary whole-word rule."""
    term = re.escape(SERVICE_TERMS[0])
    title = re.escape(SERVICE_TERMS[0].title())
    return (
        re.compile(rf"\b{term}_[A-Za-z0-9_]+\s*="),
        re.compile(rf"\b(?:[A-Z][A-Za-z0-9]*)?{title}(?:[A-Z][A-Za-z0-9]*)?\b"),
        re.compile(rf"\b[a-z0-9]+_{term}(?:_[a-z0-9_]+)?\s*="),
        re.compile(rf"\b[A-Za-z0-9]+-{term}s?\b", re.IGNORECASE),
    )


def patterns():
    return patterns_for(RESERVED)


def design_files(root: Path, explicit):
    if explicit:
        for raw in explicit:
            path = Path(raw)
            if path.is_dir():
                yield from (p for p in sorted(path.rglob("*")) if p.is_file())
            elif path.is_file():
                yield path
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(ALLOWED_PREFIXES) or rel in ALLOWED_FILES:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def under_prefix(rel: str, prefixes) -> bool:
    return any(rel.startswith(prefix) for prefix in prefixes)


def service_files(root: Path, explicit=()):
    candidates = []
    if explicit:
        for raw in explicit:
            path = Path(raw).resolve()
            if path.is_dir():
                candidates.extend(p for p in path.rglob("*") if p.is_file())
            elif path.is_file():
                candidates.append(path)
    else:
        for name in ALLOWED_FILES:
            path = root / name
            if path.is_file():
                candidates.append(path)
        for prefix in ALLOWED_PREFIXES:
            base = root / prefix.rstrip("/")
            if base.is_file():
                candidates.append(base)
            elif base.is_dir():
                candidates.extend(p for p in base.rglob("*") if p.is_file())
    suffixes = TEXT_SUFFIXES | set(CODE_SUFFIXES)
    for path in sorted(set(candidates)):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() in suffixes:
            yield path, under_prefix(rel, SERVICE_HOMES)


def main() -> int:
    parser = argparse.ArgumentParser(description="dev-lane vocabulary wall")
    parser.add_argument("--root", default=".")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    compiled = patterns()
    hits, scanned = [], 0

    for path in design_files(root, args.paths):
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for term, pattern in compiled:
                if pattern.search(line):
                    try:
                        shown = path.relative_to(root).as_posix()
                    except ValueError:
                        shown = str(path)
                    hits.append(f"{shown}:{number}: {term!r} in: {line.strip()[:90]}")
                    break

    print("pass 1")
    failed = bool(hits)
    if hits:
        print(f"vocabulary wall: {len(hits)} dev-lane term(s) in the design tree")
        for hit in hits:
            print(f"  {hit}")
        print("These words belong under .dev/ only (PLAN §10).")
    elif scanned == 0:
        # A tree with no design surface yet is vacuously clean, and
        # sandboxes rely on that — but it must not SAY clean, because
        # the repo's own wall shrinks every time a prefix or file is
        # allowed, and "nobody looked" reads exactly like "we checked"
        # once it does. test_vocabulary_wall pins that the real tree
        # never reaches this line.
        print("vocabulary wall: nothing to scan — 0 design file(s);"
              " no dev-lane term can be present, and none was looked for")
    else:
        print(f"vocabulary wall: clean — {scanned} design file(s) scanned,"
              f" {len(RESERVED)} reserved term(s)")

    service_patterns = patterns_for(SERVICE_TERMS)
    prod_patterns = patterns_for(PROD_TERMS)
    compound_patterns = compound_service_patterns()
    service_hits, service_scanned = [], 0
    for path, exempt in service_files(root, args.paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # Homes are read so the scan count is evidence of files actually
        # opened; their contents are deliberately outside this rule.
        service_scanned += 1
        if exempt:
            continue
        if path.stem.casefold() in SERVICE_TERMS:
            shown = path.relative_to(root).as_posix()
            service_hits.append(
                f"{SERVICE_RULE}: {shown}:0: "
                f"{SERVICE_TERMS[0]!r} in: path component {path.name!r}")
        shown = path.relative_to(root).as_posix()
        if any(part.casefold() in PROD_TERMS for part in Path(shown).parts):
            service_hits.append(
                f"{PROD_RULE}: {shown}:0: "
                f"{PROD_TERMS[0]!r} in: path component")
        for number, line in enumerate(text.splitlines(), 1):
            patterns_here = [
                (SERVICE_RULE, SERVICE_TERMS[0], pattern)
                for _term, pattern in service_patterns
            ]
            patterns_here.extend(
                (PROD_RULE, PROD_TERMS[0], pattern)
                for _term, pattern in prod_patterns
            )
            rel = path.relative_to(root).as_posix()
            defines_rule = (
                rel == ".dev/app/workflow/checks/vocabulary_wall.py"
                or rel.startswith(".dev/app/workflow/tests/")
            )
            if not defines_rule and path.suffix.lower() in {".py", ".json", ".yaml", ".yml"}:
                patterns_here.extend(
                    (SERVICE_RULE, SERVICE_TERMS[0], pattern)
                    for pattern in compound_patterns
                )
            for rule, term, pattern in patterns_here:
                if pattern.search(line):
                    shown = path.relative_to(root).as_posix()
                    service_hits.append(
                        f"{rule}: {shown}:{number}: "
                        f"{term!r} in: {line.strip()[:90]}")
                    break
    print("pass 2")
    if service_hits:
        failed = True
        for hit in service_hits:
            print(hit)
    elif service_scanned == 0:
        print("vocabulary wall: nothing to scan — 0 file(s); compose term was not looked for")
    else:
        print(f"vocabulary wall: clean — {service_scanned} file(s) scanned,"
              f" {len(SERVICE_TERMS)} compose term(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
