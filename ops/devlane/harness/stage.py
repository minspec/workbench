"""Build a role's snapshot from a manifest, and prove the firewall held.

The method these dispatches rely on is that two authors work from
different halves of the truth and never see each other's half. One
writes a contract from the specification without the code; another
describes the code without the specification. When they disagree, the
disagreement is real, because neither was in a position to quietly
reconcile it.

That only holds if the withholding actually happened. And withholding
is invisible: a snapshot that leaked the wrong file looks exactly like
one that did not, right up until an agreement between the two halves
turns out to mean nothing.

Measured on 2026-08-22, staging four roles by hand: the "given" and
"withheld" sets were a prose table in the plan, implemented with `cp`
and `rm`, and checked afterwards with two ad-hoc `find` commands that
had to be remembered. The same round, the conductor firewalled one
side of a pair and the planner had specified both -- so the hand
version was not merely unproven, it was wrong.

So the manifest is the input, staging is derived from it, and the
proof is not optional:

    m = load(path)
    stage(m, "extractor", dest)      # copies exactly the given set
    prove(m, "extractor", dest)      # raises unless withheld is absent

`stage` refuses to return a directory it cannot prove. There is no
argument that skips the check, because a caller in a hurry is exactly
who would pass it.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
from pathlib import Path

# --------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------
#
# {
#   "source": ".",                     # tree the given patterns resolve against
#   "roles": {
#     "extractor": {
#       "harness": "claude",
#       "model": "opus", "effort": "xhigh",
#       "sandbox": "workspace-write",
#       "given":    ["ops/devlane/workflow/**", "SCOPE.md", "SPEC.md"],
#       "withheld": ["**/PLAN.md", "cue/**", "faults/**"],
#       "returns":  ["contracts/extract/*.py", "EXTRACTION-NOTES.md"]
#     }
#   }
# }
#
# `given` and `withheld` are both globs over the staged tree. They are
# allowed to overlap: "everything under the app, except its spec" is the
# common case, and expressing it as an exception is clearer than
# enumerating 48 paths. WITHHELD ALWAYS WINS -- a file matching both is
# not staged. Reversing that precedence would make a broad `given` able
# to silently defeat a narrow `withheld`, which is the failure this
# module exists to prevent.


# Build noise: present in a working tree, never part of what a role was
# meant to read. Staging a working tree instead of a git archive picked up
# 38 __pycache__ files and a lock file alongside the 49 real ones.
#
# Excluding them by default is right, but a rule that silently drops files
# is the very thing this module exists to prevent -- so plan_files REPORTS
# what noise it dropped rather than quietly omitting it, and a manifest can
# set "include_noise": true to switch the rule off. A `given` pattern that
# names a noise path explicitly also wins, so nothing is unreachable.
NOISE = ["**/__pycache__/**", "**/*.pyc", "**/*.pyo", "**/*.lock",
         "**/.DS_Store", "**/*.egg-info/**", "**/.pytest_cache/**"]


class FirewallBreach(Exception):
    """A withheld pattern matched a file that was staged anyway.

    Not a warning. A snapshot in this state produces findings that
    cannot be trusted, and the cheapest moment to stop is before the
    dispatch rather than after reading its report.
    """


class ManifestError(Exception):
    """The manifest does not describe a role that can be staged."""


def load(path):
    m = json.loads(Path(path).read_text())
    for name, role in m.get("roles", {}).items():
        for key in ("given", "withheld"):
            if key not in role:
                raise ManifestError(
                    f"role {name!r} has no {key!r}. An absent withheld list "
                    f"is not an empty one -- say [] to mean 'nothing is "
                    f"withheld' so that the intent is on the record.")
    return m


def _role(manifest, name):
    try:
        return manifest["roles"][name]
    except KeyError:
        raise ManifestError(
            f"no role {name!r} in manifest; have "
            f"{sorted(manifest.get('roles', {}))}") from None


def _matches(rel, patterns):
    """True if `rel` matches any glob.

    A directory pattern is taken to mean everything beneath it, so
    `cue/**` covers `cue/schema.cue` and a bare `cue` does too. Being
    generous here is the safe direction: over-matching a WITHHELD
    pattern withholds too much, which fails loudly when the role finds
    a file missing. Under-matching leaks, which fails silently.
    """
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
        if fnmatch.fnmatch(rel, pat.rstrip("/") + "/**"):
            return True
        # fnmatch's `*` crosses separators, but `**/x` does not match a
        # bare `x` at the root; handle that spelling explicitly.
        if pat.startswith("**/") and fnmatch.fnmatch(rel, pat[3:]):
            return True
    return False


def plan_files(manifest, name, source=None):
    """(staged, withheld, noise) — copied, firewalled out, dropped as build noise.

    Pure: reads the source tree, writes nothing. Call it to see what a
    dispatch would contain before making one.
    """
    role = _role(manifest, name)
    src = Path(source or manifest.get("source", "."))
    drop_noise = not manifest.get("include_noise", False)
    staged, withheld, noise = [], [], []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(src))
        if any(part == ".git" for part in p.relative_to(src).parts):
            continue
        if not _matches(rel, role["given"]):
            continue
        if _matches(rel, role["withheld"]):
            withheld.append(rel)          # given, but explicitly excluded
        elif drop_noise and _matches(rel, NOISE) and rel not in role["given"]:
            noise.append(rel)             # dropped, and said so
        else:
            staged.append(rel)
    return staged, withheld, noise


def prove(manifest, name, dest):
    """Assert the firewall held. Raises FirewallBreach if it did not.

    Two properties, and the second is the one a byte-count would miss:

    - nothing matching a withheld pattern is present, and
    - something matching each given pattern IS present.

    The second exists because an empty snapshot trivially satisfies the
    first. A role staged from a mistyped path would leak nothing and
    also contain nothing, and the agent would then answer a question
    about an empty directory -- confidently, and in whichever direction
    happens to be wrong. That is the same shape as a planted fault that
    silently failed to plant.
    """
    role = _role(manifest, name)
    dest = Path(dest)
    present = [str(p.relative_to(dest)) for p in sorted(dest.rglob("*"))
               if p.is_file() and ".git" not in p.relative_to(dest).parts]

    leaked = [r for r in present if _matches(r, role["withheld"])]
    if leaked:
        raise FirewallBreach(
            f"role {name!r}: {len(leaked)} withheld file(s) were staged: "
            f"{leaked[:5]}{'...' if len(leaked) > 5 else ''}")

    if not present:
        raise FirewallBreach(
            f"role {name!r}: nothing was staged. An empty snapshot "
            f"withholds everything and proves nothing.")

    unmet = [pat for pat in role["given"]
             if not any(_matches(r, [pat]) for r in present)]
    if unmet:
        raise FirewallBreach(
            f"role {name!r}: given pattern(s) matched no staged file: "
            f"{unmet}. Either the pattern is wrong or withheld swallowed "
            f"it; both produce a role missing what it was promised.")
    return present


def stage(manifest, name, dest, source=None):
    """Build the snapshot and prove it. Returns the staged file list.

    The proof runs here, not as a step a caller may skip.
    """
    _role(manifest, name)   # fail on an unknown role before touching disk
    src = Path(source or manifest.get("source", "."))
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ManifestError(
            f"{dest} is not empty; stage into a fresh directory so that "
            f"what is present is what this manifest put there.")
    staged, _withheld, _noise = plan_files(manifest, name, src)
    for rel in staged:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, out)
    return prove(manifest, name, dest)


def dispatch_spec(manifest, name):
    """The harness, model, effort and sandbox this role is dispatched with.

    Kept next to the firewall on purpose: `isolation.py` decides what a
    harness may bring from the operator's machine, this decides what the
    snapshot contains, and both have to be right for one dispatch to
    mean anything.
    """
    role = _role(manifest, name)
    missing = [k for k in ("harness",) if k not in role]
    if missing:
        raise ManifestError(f"role {name!r} lacks {missing}")
    return {k: role.get(k) for k in
            ("harness", "model", "effort", "sandbox", "returns")}
