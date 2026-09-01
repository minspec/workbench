#!/usr/bin/env python3
"""Derive the CI check-run names a ruleset would list.

The check-run API reports job names, with a matrix value in parentheses
when the job name does not interpolate it. This script prints that list
from the two workflow files plus `wf --json status --checks`.

    wf --json status --checks > checks.json
    ci_contexts.py checks.json
    ci_contexts.py checks.json --json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# checks/ -> workflow -> app -> .dev -> repo.
# Same root as support.WF_DIR.parent.parent.parent, from __file__.
ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

DEV = "ci-dev.yml"
APPS = "ci-dev-apps.yml"

# Quoted `name:` at job indent. Workflow `name: CI` is unquoted and
# unindented; step names in these files are unquoted or dashed.
JOB_NAME = re.compile(r'(?m)^    name: "([^"]+)"')
JOB_ID_TEXT = r"[A-Za-z_][A-Za-z0-9_-]*"
JOB_ID = re.compile(rf"(?m)^  ({JOB_ID_TEXT}):$")
JOB_HEADER = re.compile(
    rf"^  (?:(?P<plain>{JOB_ID_TEXT})|"
    rf"\"(?P<double>{JOB_ID_TEXT})\"|"
    rf"'(?P<single>{JOB_ID_TEXT})'):"
    r"\s*(?:#.*)?$"
)
SHARD_LINE = re.compile(r"(?m)^[ \t]+shard:\s*\[([^]]*)\]")
NAME_LINE = re.compile(r'(?m)^    name: "([^"]*)"$')

# Shape of each known job, keyed by file and job id (`^  <id>:$`).
# The emitted text comes from the file's `name:` line; this table pins
# how that line is expanded, not the string itself.
KNOWN = {
    DEV: {
        "verify": "static",
        "discover": "static",
        "gates": "gates",
    },
    APPS: {
        "workflow": "shards",
        "app": "apps",
    },
}

APP_NAME = "dev: ${{ matrix.app }}"
DISCOVER_MATRIX = "fromJSON(needs.discover.outputs.matrix)"
STATIC_NAME = 'quoted name: with no ${{'


class DerivationError(Exception):
    """A short list is never a valid answer to a broken input."""

    def __init__(self, path, job_id, expected, found):
        self.path = path
        self.job_id = job_id
        self.expected = expected
        self.found = found

    def __str__(self):
        return (
            f"{self.path}: job {self.job_id}: "
            f"expected {self.expected}, found {self.found}"
        )


def workflow_path(root, filename):
    return Path(root) / ".github" / "workflows" / filename


def read_workflow(root, filename):
    path = workflow_path(root, filename)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DerivationError(
            filename, "-", "readable workflow file", "missing"
        ) from None
    except OSError as err:
        raise DerivationError(
            filename, "-", "readable workflow file", str(err)
        ) from err


def jobs_section(text):
    parts = re.split(r"(?m)^jobs:\s*$", text, maxsplit=1)
    if len(parts) != 2:
        return ""
    return parts[1]


def _job_blocks(text, filename="workflow"):
    """Return every job block, or refuse a job shape we cannot parse.

    GitHub accepts mixed-case and underscore job ids, plus YAML-quoted
    keys. The old parser only used its narrow match both to inventory jobs
    and to find block boundaries, so an accepted-but-unmatched job could be
    omitted or swallowed into the preceding known job. Parse every supported
    GitHub id spelling before deciding whether the id is known.
    """
    section = jobs_section(text)
    lines = section.splitlines(keepends=True)
    headers = []
    for index, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw == raw.lstrip():
            # A later top-level key ends `jobs:`.
            lines = lines[:index]
            break
        if not raw.startswith("  ") or raw.startswith(("   ", "\t")):
            continue
        match = JOB_HEADER.fullmatch(raw)
        if not match:
            raise DerivationError(
                filename,
                raw.strip().split(":", 1)[0].strip("'\"") or "-",
                "a GitHub job id mapping on its own line",
                raw.strip(),
            )
        job_id = next(value for value in match.groupdict().values() if value)
        headers.append((index, job_id))

    blocks = {}
    for position, (start, job_id) in enumerate(headers):
        if job_id in blocks:
            raise DerivationError(
                filename, job_id, "unique job id", "duplicate"
            )
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        blocks[job_id] = "".join(lines[start:end])
    return blocks


def job_ids(text, filename="workflow"):
    return list(_job_blocks(text, filename))


def job_block(text, job_id, filename="workflow"):
    return _job_blocks(text, filename).get(job_id)


def job_name(block):
    if block is None:
        return None
    match = NAME_LINE.search(block)
    if not match:
        return None
    return match.group(1)


def app_matrix_entries(text):
    """(app, cmd) pairs from the apps workflow's include matrix."""
    job = job_block(text, "app", APPS)
    if job is None:
        return []
    start = job.find("include:")
    if start == -1:
        return []
    rest = job[start + len("include:"):]
    cut = rest.find("\n    steps:")
    block = rest[:cut] if cut != -1 else rest
    entries = []
    app = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        named = re.fullmatch(
            r"          - app:\s+([A-Za-z0-9_-]+)", line
        )
        if named:
            if app is not None:
                raise DerivationError(
                    APPS, "app", f"cmd: for include app {app}", "absent"
                )
            app = named.group(1)
            continue
        command = re.fullmatch(r"            cmd:\s+(.+)", line)
        if command and app is not None:
            entries.append((app, command.group(1).strip()))
            app = None
            continue
        raise DerivationError(
            APPS,
            "app",
            "canonical '- app:' then 'cmd:' include rows",
            stripped,
        )
    if app is not None:
        raise DerivationError(
            APPS, "app", f"cmd: for include app {app}", "absent"
        )
    return entries


def shard_values(text):
    """Shard indexes from the workflow job's `shard: [ … ]` line."""
    job = job_block(text, "workflow", APPS)
    if job is None:
        return None
    match = SHARD_LINE.search(job)
    if not match:
        return None
    inner = match.group(1).strip()
    if not inner:
        return []
    return [item.strip() for item in inner.split(",") if item.strip() != ""]


def require_quoted_static(filename, job_id, name):
    if name is None:
        raise DerivationError(filename, job_id, STATIC_NAME, "absent")
    if "${{" in name:
        raise DerivationError(filename, job_id, STATIC_NAME, name)
    return name


def _ci_matrix(payload):
    if not isinstance(payload, dict):
        raise DerivationError(
            "checks.json",
            "ci_matrix",
            "object whose ci_matrix is a non-empty list of strings",
            type(payload).__name__,
        )
    matrix = payload.get("ci_matrix")
    if (
        not isinstance(matrix, list)
        or not matrix
        or not all(isinstance(item, str) for item in matrix)
    ):
        raise DerivationError(
            "checks.json",
            "ci_matrix",
            "non-empty list of strings",
            repr(matrix),
        )
    return matrix


def derive(payload, root=None):
    """Return the sorted check-run names, or raise DerivationError."""
    root = Path(root) if root is not None else ROOT
    matrix = _ci_matrix(payload)
    names = []
    texts = {filename: read_workflow(root, filename) for filename in KNOWN}
    for filename, kinds in KNOWN.items():
        text = texts[filename]
        found_ids = job_ids(text, filename)
        extras = [jid for jid in found_ids if jid not in kinds]
        if extras:
            raise DerivationError(
                filename,
                extras[0],
                "a job id in the known table",
                "not in the table",
            )
        for job_id, kind in kinds.items():
            block = job_block(text, job_id, filename)
            if block is None:
                raise DerivationError(
                    filename, job_id, f"^  {job_id}:$ line", "absent"
                )
            name = job_name(block)
            if kind == "static":
                names.append(require_quoted_static(filename, job_id, name))
            elif kind == "gates":
                static = require_quoted_static(filename, job_id, name)
                if DISCOVER_MATRIX not in block:
                    raise DerivationError(
                        filename,
                        job_id,
                        DISCOVER_MATRIX,
                        "matrix does not reference discover outputs",
                    )
                names.extend(f"{static} ({check})" for check in matrix)
            elif kind == "shards":
                static = require_quoted_static(filename, job_id, name)
                shards = shard_values(text)
                if shards is None:
                    raise DerivationError(
                        filename,
                        job_id,
                        "shard: [ … ] list in matrix",
                        "absent",
                    )
                if not shards:
                    raise DerivationError(
                        filename, job_id, "non-empty shard list", "[]"
                    )
                names.extend(f"{static} ({shard})" for shard in shards)
            elif kind == "apps":
                if name != APP_NAME:
                    raise DerivationError(
                        filename,
                        job_id,
                        f'name: "{APP_NAME}"',
                        "absent" if name is None else name,
                    )
                entries = app_matrix_entries(text)
                apps = [app for app, _cmd in entries]
                if not apps:
                    raise DerivationError(
                        filename,
                        job_id,
                        "- app: entries in the include matrix",
                        "none",
                    )
                dupes = sorted({app for app in apps if apps.count(app) > 1})
                if dupes:
                    raise DerivationError(
                        filename,
                        job_id,
                        "unique - app: values",
                        f"duplicate {dupes[0]}",
                    )
                names.extend(
                    name.replace("${{ matrix.app }}", app) for app in apps
                )
            else:
                raise DerivationError(filename, job_id, "known kind", kind)
    return sorted(names)


def main(argv) -> int:
    args = argv[1:]
    as_json = False
    if len(args) == 2 and args[1] == "--json":
        as_json = True
        args = args[:1]
    if len(args) != 1 or args[0].startswith("-"):
        print(__doc__.strip(), file=sys.stderr)
        return 64
    try:
        with open(args[0], encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as err:
        print(
            f"checks.json: job ci_matrix: expected JSON object, found {err}",
            file=sys.stderr,
        )
        return 2
    except OSError as err:
        print(
            f"checks.json: job ci_matrix: expected readable file, found {err}",
            file=sys.stderr,
        )
        return 2
    try:
        names = derive(payload)
    except DerivationError as err:
        print(err, file=sys.stderr)
        return 2
    if as_json:
        json.dump(names, sys.stdout)
        sys.stdout.write("\n")
    else:
        sys.stdout.write("".join(f"{name}\n" for name in names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
