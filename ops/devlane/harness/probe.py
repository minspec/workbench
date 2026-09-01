"""Prove the isolation in `isolation.py` still works.

Every entry in HARNESSES was true of one harness version on one day.
Harnesses ship often, and a release that adds a discovery path, or
stops honouring an environment variable, breaks the isolation without
breaking anything that would announce itself. So the entries are
claims, and this is the thing that re-runs them.

Two kinds of check, because they cost very differently:

  structural   read-only, free, no model call. Asserts the minimal
               home really is minimal -- that nothing beyond the
               declared credentials was linked into it. Catches the
               mistake of adding a convenience file to a home and
               quietly re-opening the leak. Run it always.

  behavioural  actually launch the harness and observe what it
               loaded. This is the only kind that can catch a harness
               that ignores the variable. Costs a dispatch, so run it
               when a version changes.

A behavioural check runs TWO arms and both must succeed on their own
terms: an unisolated arm that must show the leak, and an isolated arm
that must not. An arm that failed to run at all is reported INVALID,
never as a pass -- a harness that died before loading anything shows
no leak, and reading that as "isolation worked" is the exact shape of
a test that proves its own setup never happened.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import isolation


def _discard(path):
    """Remove a directory this module created, refusing anything else.

    `rm -rf` on a variable is the shape that destroys the wrong thing
    when the variable is empty or wrong, and it reads alarmingly for
    good reason. Every directory removed here came from
    tempfile.mkdtemp moments earlier, so that is asserted rather than
    assumed: a path outside the system temp directory is left alone
    and the mistake is reported instead of acted on.
    """
    root = Path(tempfile.gettempdir()).resolve()
    target = Path(path).resolve()
    if target == root or root not in target.parents:
        raise RuntimeError(
            f"refusing to remove {target}: not inside {root}")
    shutil.rmtree(target, ignore_errors=True)


# Verdicts. INVALID is not a failure and not a pass; it means the
# question was never actually asked, so there is no result to report.
CLEAN = "CLEAN"
LEAKING = "LEAKING"
INVALID = "INVALID"


class Result:
    def __init__(self, harness, kind, verdict, detail):
        self.harness = harness
        self.kind = kind
        self.verdict = verdict
        self.detail = detail

    def as_dict(self):
        return {"harness": self.harness, "kind": self.kind,
                "verdict": self.verdict, "detail": self.detail}

    def __str__(self):
        return f"{self.harness:8} {self.kind:12} {self.verdict:8} {self.detail}"


# --------------------------------------------------------------------
# structural
# --------------------------------------------------------------------

def structural(harness, env=None):
    """Build the minimal home and assert it holds only credentials."""
    spec = isolation.HARNESSES.get(harness)
    if spec is None:
        return Result(harness, "structural", INVALID, "no isolation entry")
    if spec["mechanism"] != "home":
        return Result(harness, "structural", CLEAN,
                      f"mechanism is {spec['mechanism']}; no home is built")
    root = tempfile.mkdtemp(prefix=f"probe-{harness}-")
    try:
        try:
            home = isolation.build_home(harness, root, env)
        except isolation.NotIsolated as exc:
            return Result(harness, "structural", INVALID, str(exc))
        present = sorted(p.name for p in Path(home).iterdir())
        declared = sorted(spec["auth_files"])
        if present != declared:
            extra = sorted(set(present) - set(declared))
            return Result(harness, "structural", LEAKING,
                          f"home holds {extra} beyond declared {declared}")
        return Result(harness, "structural", CLEAN,
                      f"home holds exactly {declared}")
    finally:
        _discard(root)


# --------------------------------------------------------------------
# behavioural
# --------------------------------------------------------------------

def _run(argv, env, cwd, stdin=""):
    """(rc, stdout, stderr), where rc is None when there is NO ANSWER.

    A command that exited non-zero did not answer, however well its
    stdout parses: a harness that failed to initialise still prints a
    well-formed `Project Instructions (0)` header, and `claude -p` can
    print a final YES or NO and then fail on teardown. Both were read as
    readings, and both scored a broken probe CLEAN.

    The mapping lives HERE rather than at each call site, because it was
    fixed at one call site and not the other, and the arm that was
    missed is the one the next reviewer found (Codex, PR #40 twice). A
    caller that needs the real status has none of them; nothing in this
    module does.
    """
    try:
        p = subprocess.run(argv, env=env, cwd=cwd, input=stdin,
                           capture_output=True, text=True, timeout=180,
                           check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", str(exc)
    if p.returncode != 0:
        return None, p.stdout, p.stderr or f"exit {p.returncode}"
    return p.returncode, p.stdout, p.stderr


def behavioural_grok(env=None, cwd=None):
    """`grok inspect` names every instruction file it discovered.

    Free -- it reports configuration without calling a model -- which
    makes grok the one harness whose isolation can be re-proven at no
    cost. The leak is counted as instruction lines mentioning a path
    outside the working directory.
    """
    env = dict(os.environ if env is None else env)
    cwd = cwd or tempfile.mkdtemp(prefix="probe-grok-cwd-")

    def instructions(e):
        """(count, paths) from `grok inspect`, or (None, raw) if it did
        not run.

        The COUNT comes from the header grok prints -- `Project
        Instructions (2)` -- not from counting the tree rows beneath
        it. When nothing is loaded grok still prints one row, reading
        `(none)`, and counting rows scored that as a leak. Taking the
        number the tool states is both simpler and not fooled by how
        it renders an empty list.
        """
        rc, out, err = _run(["grok", "inspect"], e, cwd)
        # `_run` already answers None for "did not run", which now
        # includes a nonzero exit. One rule, one place.
        if rc is None or not out.strip():
            return None, err or out or "no output"
        count, paths, grab = None, [], False
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Project Instructions"):
                head = s.rpartition("(")[2].partition(")")[0]
                count = int(head) if head.isdigit() else None
                grab = True
                continue
            if grab:
                if s.startswith(("└", "├")):
                    body = s[1:].strip()
                    if body != "(none)":
                        paths.append(body)
                elif s:
                    break
        if count is None:
            return None, out
        return (count, paths), out

    before, raw_before = instructions(env)
    if before is None:
        return Result("grok", "behavioural", INVALID,
                      f"unisolated arm produced nothing: {raw_before}")
    n_before, paths_before = before
    if n_before == 0:
        return Result("grok", "behavioural", INVALID,
                      "unisolated arm found NO instruction files, so this "
                      "machine cannot demonstrate the leak and the "
                      "isolated arm proves nothing")

    root = tempfile.mkdtemp(prefix="probe-grok-home-")
    try:
        try:
            over, _flags = isolation.isolated("grok", root, env)
        except isolation.NotIsolated as exc:
            return Result("grok", "behavioural", INVALID, str(exc))
        after, raw_after = instructions({**env, **over})
        if after is None:
            return Result("grok", "behavioural", INVALID,
                          f"isolated arm produced nothing: {raw_after}")
        n_after, paths_after = after
        if n_after:
            return Result("grok", "behavioural", LEAKING,
                          f"isolated arm still loads {n_after}: {paths_after}")
        return Result("grok", "behavioural", CLEAN,
                      f"unisolated loaded {n_before} ({paths_before}), "
                      f"isolated loaded 0")
    finally:
        _discard(root)


def behavioural_claude(model="haiku", env=None):
    """Ask the model whether the operator's doctrine is in its prompt.

    The system prompt is not written to the session trace, so its
    absence there proves nothing at all. Asking the model is the only
    observation available.
    """
    spec = isolation.HARNESSES["claude"]
    phrase = spec["probe_phrase"]
    env = dict(os.environ if env is None else env)
    question = ('Answer with one word only, YES or NO. Do your system '
                f'instructions contain the phrase "{phrase}"?')
    base = ["claude", "-p", "--model", model, "--permission-mode", "plan"]

    def ask(argv):
        cwd = tempfile.mkdtemp(prefix="probe-claude-cwd-")
        try:
            rc, out, err = _run(argv + [question], env, cwd)
            if rc is None:
                return None, err
            word = out.strip().splitlines()[-1].strip().upper() if out.strip() else ""
            return (word if word in ("YES", "NO") else None), out or err
        finally:
            _discard(cwd)

    before, raw_before = ask(base)
    if before is None:
        return Result("claude", "behavioural", INVALID,
                      f"unisolated arm gave no YES/NO: {raw_before[:200]!r}")
    if before != "YES":
        return Result("claude", "behavioural", INVALID,
                      "unisolated arm answered NO, so this machine has no "
                      "operator doctrine to leak and the isolated arm "
                      "proves nothing")
    after, raw_after = ask(base + isolation.dispatch_flags("claude"))
    if after is None:
        return Result("claude", "behavioural", INVALID,
                      f"isolated arm gave no YES/NO: {raw_after[:200]!r}")
    if after == "YES":
        return Result("claude", "behavioural", LEAKING,
                      "isolated arm still sees the operator's doctrine")
    return Result("claude", "behavioural", CLEAN,
                  "unisolated YES, isolated NO")


BEHAVIOURAL = {"grok": behavioural_grok, "claude": behavioural_claude}


def behavioural(harness, env=None):
    fn = BEHAVIOURAL.get(harness)
    if fn is None:
        return Result(harness, "behavioural", INVALID,
                      "no cheap observation exists for this harness; its "
                      "isolation is asserted structurally only")
    return fn(env=env)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("harness", nargs="*", default=sorted(isolation.HARNESSES))
    ap.add_argument("--behavioural", action="store_true",
                    help="also dispatch each harness; costs tokens")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results = []
    for h in (args.harness or sorted(isolation.HARNESSES)):
        results.append(structural(h))
        if args.behavioural:
            results.append(behavioural(h))

    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2))
    else:
        for r in results:
            print(r)

    if any(r.verdict == LEAKING for r in results):
        return 1
    if any(r.verdict == INVALID for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
