#!/usr/bin/env python3
"""Turn `wf status --checks --json` into GitHub Actions matrix outputs.

PLAN §7 says the matrix should be derived from that command. This is the
one-line transformation that does it, kept as a script rather than inlined in
YAML so it can be tested — a shell one-liner in a workflow file is the part
nobody can run locally and nobody notices breaking.

    wf --json status --checks > checks.json
    ci_matrix.py checks.json >> "$GITHUB_OUTPUT"

Writes `matrix=<json array>` and `any=yes|no`. The `any` flag exists because a
matrix job with an empty matrix is a hard error in Actions, not a skip.
"""

from __future__ import annotations

import json
import sys


def outputs(payload: dict) -> str:
    names = payload.get("ci_matrix") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise SystemExit(f"ci_matrix must be a list of names, got {names!r}")
    return (
        f"matrix={json.dumps(sorted(set(names)))}\n"
        f"any={'yes' if names else 'no'}\n"
    )


def require_workspace_tests(payload: dict, root) -> None:
    """A Cargo workspace whose test check is not in the matrix is refused.

    cargo-test ships with "ci": false because no workspace exists yet, and
    nothing would force the product PR that adds Cargo.toml to remember the
    flag — so broken Rust could pass every gate with zero product tests run.
    This runs in the discover job, which is unfiltered and therefore runs on
    exactly that PR; the refusal names the flag to flip.
    """
    from pathlib import Path

    root = Path(root)
    manifests = [p for p in [root / "Cargo.toml", *root.glob("crates/*/Cargo.toml")]
                 if p.exists()]
    if not manifests:
        return
    in_matrix = set(payload.get("ci_matrix") or [])
    for check in payload.get("checks") or []:
        argv = check.get("argv") or []
        if argv[:2] == ["cargo", "test"] and check.get("name") not in in_matrix:
            raise SystemExit(
                f"{manifests[0]} exists but the registered test check "
                f"{check.get('name')!r} is not in the CI matrix — set "
                f"\"ci\": true on it in the {check.get('kind')}@"
                f"{check.get('kind_version')} gate-kind spec. A workspace "
                f"whose tests CI never runs is a gate nobody notices is "
                f"missing."
            )


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 64
    with open(argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
    require_workspace_tests(payload, ".")
    sys.stdout.write(outputs(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
