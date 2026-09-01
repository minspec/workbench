#!/usr/bin/env python3
"""Lint, as a registered check.

Runs ruff under the repo's pinned contract (`ruff.toml`) so the CI
verdict and the local one are the same verdict. There is no rule
selection here on purpose: a checker that carried its own list would
be a second contract, free to drift from the file everyone edits.

Refusals, not silences. A lint pass is INVALID — exit 1, not 0 —
when ruff is absent, when it cannot be run, or when the paths given
match no Python file at all. "Nobody looked" must never render as
"we checked and it was fine".

    lint.py [--root DIR] [PATH ...]

With no PATH, checks the whole tree from --root, which the config's
excludes then narrow.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: Only these are Python; `--show-files` lists whatever the config
#: reaches, including ruff.toml itself, so the count must filter.
PY_SUFFIXES = (".py", ".pyi")

#: A finding line is `path:line:col: CODE message`; ruff also prints a
#: summary and fix advice, which are not findings.
FINDING = re.compile(r"^.+?:\d+:\d+: [A-Z]+\d+")


def run_ruff(root: Path, args: list[str]):
    try:
        return subprocess.run(
            ["ruff", *args], cwd=root,
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        print(f"lint: INVALID — ruff could not be run ({exc})")
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ruff as a registered check")
    parser.add_argument("--root", default=".")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    root = Path(args.root)
    paths = args.paths or ["."]

    # The pinned contract is the whole point: without the config ruff
    # falls back to its own defaults and can report clean under rules
    # nobody chose, which is the drift this check exists to prevent.
    config = root / "ruff.toml"
    if not config.is_file():
        print(f"lint: INVALID — no ruff.toml under {root}; the pinned"
              " contract is the only thing that makes a verdict mean"
              " anything")
        return 1
    common = ["--config", str(config)]

    listed = run_ruff(root, ["check", *common, "--show-files", *paths])
    if listed is None:
        return 1
    if listed.returncode not in (0, 1):
        print("lint: INVALID — ruff refused the request:")
        print(listed.stderr.strip() or listed.stdout.strip())
        return 1
    files = [line for line in listed.stdout.splitlines()
             if line.strip().endswith(PY_SUFFIXES)]
    if not files:
        print(f"lint: INVALID — {paths} matched zero Python files;"
              " a lint pass over nothing is not a clean lint pass")
        return 1

    proc = run_ruff(root, ["check", *common, "--output-format", "concise",
                           *paths])
    if proc is None:
        return 1
    if proc.returncode not in (0, 1):
        print("lint: INVALID — ruff exited abnormally:")
        print(proc.stderr.strip() or proc.stdout.strip())
        return 1
    if proc.returncode == 1:
        findings = [ln for ln in proc.stdout.splitlines() if FINDING.match(ln)]
        print(f"lint: {len(findings)} finding(s)"
              f" across {len(files)} file(s)")
        print(proc.stdout.rstrip())
        return 1

    version = run_ruff(root, ["--version"])
    stamp = version.stdout.strip() if version else "ruff"
    print(f"lint: clean — {len(files)} file(s), {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
