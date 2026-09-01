#!/usr/bin/env python3
"""Generate the controls for context.cue, so none of them can be wrong by hand.

    python3 ops/devlane/harness/controls/build.py [--check]

The positive control was written by hand and encoded five separate
untruths about the very module it describes: flags the launcher does not
pass, an environment variable a flags-mechanism harness never sets, a
version the module records as unrecorded, doctrine evidence citing one
harness's tool on another, and an interface declared in a document that
was not staged. It was also illegal under the law it was supposed to
demonstrate — its brief and its task described the same deliverable's
seam differently (Codex, PR #40).

So the facts about a harness come from `isolation.py` rather than from
memory, and every rejecting control is derived from a passing one by
changing EXACTLY ONE PATH. That second rule is the one that was missing:
a fixture that breaks two things at once still fails when only one of
the two guards exists, so it cannot witness either — measured on the
old `home-without-home`, which omitted both the home and the credential
list (Grok, second read of PR #40).

`--check` regenerates into memory and compares, so a control edited by
hand is a failure rather than a surprise.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent


def _isolation():
    spec = importlib.util.spec_from_file_location(
        "harness_isolation", HARNESS / "isolation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: A digest is an identity, not a hint; these are fixture identities and
#: say so, rather than borrowing a real file's digest and going stale.
def _digest(seed):
    import hashlib
    return hashlib.sha256(f"control-fixture:{seed}".encode()).hexdigest()


def _evidence(measured):
    """The module's own record of how isolation was established."""
    if "probe_default" in measured and "probe_isolated" in measured:
        return (f"unisolated arm answered {measured['probe_default']} to the "
                f"probe phrase, isolated arm answered "
                f"{measured['probe_isolated']}")
    leak = measured.get("leak")
    if leak:
        return f"unisolated dispatch loaded: {leak}"
    raise SystemExit("a harness with no recorded measurement cannot be a "
                     "control: there is nothing to quote")


def _doctrine(harness):
    """What a real dispatch of `harness` actually receives.

    Presence on disk is not receipt (context.cue). Claude auto-loads
    CLAUDE.md and not AGENTS.md; Codex embeds AGENTS.md and not
    CLAUDE.md; Grok loads both.
    """
    received = {
        "claude": [("CLAUDE.md", True)],
        "codex": [("AGENTS.md", True)],
        "grok": [("CLAUDE.md", True), ("AGENTS.md", True)],
    }.get(harness)
    if received is None:
        raise SystemExit(
            f"no doctrine receipt recorded for {harness!r}: a control "
            f"cannot invent one")
    return {"files": [{
        "path": path,
        "sha256": _digest(path),
        "in_force": in_force,
        "evidence": f"listed as a project instruction by {harness}",
    } for path, in_force in received]}


def context_for(iso, harness, root="/snapshot"):
    """A #Dispatchable context describing a real dispatch of `harness`."""
    spec = iso.HARNESSES[harness]
    flags = iso.dispatch_flags(harness)
    env = iso.dispatch_env(harness, home=f"{root}/.harness-home")
    measured = spec["measured"]

    isolation = {
        "mechanism": spec["mechanism"],
        "flags": flags or {"declared_absent":
                           f"{harness} suppresses discovery by home, not argv"},
        "env": env or {"declared_absent":
                       f"{harness} needs no environment override"},
        "observed": {
            "operator_config_present": False,
            # The module records its measurement differently per
            # harness — a probe pair where one was run, the observed
            # leak where it was read off the tool. Quote whichever it
            # actually holds rather than inventing a uniform sentence.
            "evidence": _evidence(measured),
            "checked_at": measured["on"],
            "harness_version": measured["version"],
        },
    }
    if spec["mechanism"] == "home":
        isolation["home"] = f"{root}/.harness-home"
        isolation["auth_files"] = list(spec["auth_files"])

    interface = {
        "name": "extractor-cli",
        "argv": ["--root", "DIR", "--out", "PATH"],
        "output": "file",
        "fields": ["facts", "unresolved"],
        # Named in a file that IS staged, or `dangling_references: []`
        # would be a lie the schema cannot catch.
        "declared_at": "PLAN.md section 5",
    }
    deliverable = {"path": "extract/cli.py", "interface": interface}

    return {
        "role": "extractor",
        "harness": {"name": harness, "version": measured["version"],
                    "isolation": isolation},
        "staged": {
            "root": root,
            "files": [{"path": "PLAN.md", "sha256": _digest("PLAN.md"),
                       "bytes": 33975}],
            "count": 1,
            "given": ["ops/devlane/workflow/PLAN.md"],
            "withheld": ["ops/devlane/workflow/**.py"],
            "noise_dropped": {"declared_absent":
                              "the source tree carried no build artefacts"},
            "proof": {"tool": "stage.py", "withheld_present": [],
                      "given_unmet": []},
        },
        "doctrine": _doctrine(harness),
        "brief": {
            "role": "extractor",
            "deliverables": [copy.deepcopy(deliverable)],
            "report_fields": ["RESULT", "UNRESOLVED"],
            "rules": ["read no code outside the snapshot"],
            "sha256": _digest("brief"), "bytes": 4096,
        },
        "task": {
            "name": "describe-the-cli",
            "requires": [{"what": "the plan's CLI section",
                          "satisfied_by": "PLAN.md",
                          "why": "the verbs come from it"}],
            # The same contract, and #Dispatchable now requires the two
            # copies to be identical rather than merely both present.
            "produces": [copy.deepcopy(deliverable)],
            "report_fields": ["RESULT", "UNRESOLVED"],
        },
        "unmet_requirements": [],
        "dangling_references": [],
        "derived_from": _digest("law"),
    }


def at(doc, path):
    node = doc
    for step in path.split("."):
        node = node[int(step)] if step.isdigit() else node[step]
    return node


def put(doc, path, value):
    steps = path.split(".")
    node = doc
    for step in steps[:-1]:
        node = node[int(step)] if step.isdigit() else node[step]
    last = steps[-1]
    if value is _DROP:
        del node[int(last) if last.isdigit() else last]
    else:
        node[int(last) if last.isdigit() else last] = value


_DROP = object()

#: (name, base, path, new value, expected rejection path, why it matters).
#: The prose is parenthesised per entry: an implicit concatenation
#: inside a collection is one missing comma away from silently merging
#: two elements, which is why the linter refuses it.
MUTANTS = [
    ("stale-observation", "home",
     "harness.isolation.observed.harness_version", "0.0.1-not-this-build",
     "harness.isolation.observed.harness_version",
     ("an isolation fact is true of one build on one day, and a release "
      "is exactly when a new discovery path appears")),
    ("empty-snapshot", "home", "staged.files", [],
     "staged.files",
     ("an empty snapshot satisfies every withholding rule perfectly, so a "
      "gate reading only the stated count cannot tell it from a firewall "
      "that worked")),
    ("home-without-home", "home", "harness.isolation.home", _DROP,
     "harness.isolation.home",
     ("for a home-mechanism harness the relocated home IS the isolation "
      "evidence; omitting it leaves a record that reads complete")),
    ("home-without-auth-files", "home", "harness.isolation.auth_files", _DROP,
     "harness.isolation.auth_files",
     ("the credential list is what makes \"and nothing else\" checkable; "
      "the old fixture dropped it together with the home and so could "
      "witness neither")),
    ("flags-not-recorded", "flags", "harness.isolation.flags",
     {"declared_absent": "not written down"},
     "harness.isolation.flags",
     ("for a flags-mechanism harness the argv is the isolation, so "
      "declaring it absent is the same missing evidence in a complete "
      "record")),
    ("role-mismatch", "home", "brief.role", "reviewer",
     "role",
     ("nothing else stops a launcher handing a reviewer's instructions "
      "to an extractor")),
    ("task-brief-interface", "home",
     "task.produces.0.interface", {"declared_absent": "nothing consumes it"},
     "task.produces.0.interface",
     ("the brief and the task state one contract twice; two authors "
      "handed different halves of it meet at a seam that does not exist")),
    ("task-brief-report-fields", "home",
     "task.report_fields", ["RESULT"],
     "task.report_fields",
     ("a report field missing from one copy is indistinguishable from an "
      "honest \"nothing to say\"")),
]


def differing_paths(a, b, prefix=""):
    """Every leaf path where two documents disagree."""
    if type(a) is not type(b):
        return [prefix or "."]
    if isinstance(a, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            where = f"{prefix}.{key}" if prefix else key
            if key not in a or key not in b:
                out.append(where)
            else:
                out += differing_paths(a[key], b[key], where)
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [prefix or "."]
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += differing_paths(x, y, f"{prefix}.{i}")
        return out
    return [] if a == b else [prefix or "."]


def _must_be_stated():
    """The list lives in vet_context.py, which enforces it. One copy."""
    spec = importlib.util.spec_from_file_location(
        "harness_vet_context", HARNESS / "vet_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MUST_BE_STATED


def build():
    iso = _isolation()
    bases = {"flags": context_for(iso, "claude"),
             "home": context_for(iso, "codex")}
    files = {f"dispatchable-{kind}.json": doc for kind, doc in bases.items()}

    for name, base, path, value, expect, why in MUTANTS:
        doc = copy.deepcopy(bases[base])
        put(doc, path, value)
        changed = differing_paths(bases[base], doc)
        # ONE axis: every difference is inside the declared subtree, and
        # there is at least one. Replacing a field whose value is an
        # object legitimately shows up as several leaf paths beneath it;
        # what must never appear is a path OUTSIDE it, because a fixture
        # that breaks two things still fails when only one of the two
        # guards exists and so witnesses neither.
        stray = [d for d in changed if d != path and not d.startswith(path + ".")]
        if stray or not changed:
            raise SystemExit(
                f"{name}: wanted changes confined to {path!r}, got {changed}")
        files[f"rejects/{name}.json"] = doc
        files[f"rejects/{name}.reason.json"] = {
            "derived_from": f"controls/dispatchable-{base}.json",
            "change": f"{path} — {'removed' if value is _DROP else 'replaced'}",
            "expect_path": expect,
            "why": why,
            "finding": "Codex, PR #40",
            "generated_by": "controls/build.py",
        }
    # An omission witness per must-be-stated path. `cue vet` ACCEPTS
    # every one of these -- that is the defect -- so their rejection
    # comes from the presence check, and the sidecar says so.
    for path in _must_be_stated():
        base = "flags" if path.startswith("harness.isolation.flags") else "home"
        doc = copy.deepcopy(bases[base])
        try:
            put(doc, path, _DROP)
        except (KeyError, IndexError, TypeError) as exc:
            raise SystemExit(f"omission witness: {path} is not in the "
                             f"{base} control, so nothing witnesses it") from exc
        name = "omits-" + path.replace(".", "-")
        files[f"rejects/{name}.json"] = doc
        files[f"rejects/{name}.reason.json"] = {
            "derived_from": f"controls/dispatchable-{base}.json",
            "change": f"{path} — omitted entirely",
            "expect_path": path,
            "checked_by": "source",
            "why": ("the law constrains this field, and a constraint is "
                    "also a default: an omitting document validates with "
                    "the clean answer supplied for it, so `cue vet` cannot "
                    "witness this and the check runs on the raw JSON"),
            "finding": "Codex, PR #40",
            "generated_by": "controls/build.py",
        }
    # ...and one for the pair the law unifies: a task that states LESS
    # than the brief inherits the rest, so the document claims an
    # agreement it never made and `cue vet` sees a complete one.
    thin = copy.deepcopy(bases["home"])
    thin["task"]["produces"] = [{"path": thin["brief"]["deliverables"][0]["path"]}]
    files["rejects/task-states-less-than-brief.json"] = thin
    files["rejects/task-states-less-than-brief.reason.json"] = {
        "derived_from": "controls/dispatchable-home.json",
        "change": "task.produces states only the path; the interface is left "
                  "for the law to copy across from the brief",
        "expect_path": "task.produces",
        "checked_by": "source",
        "why": ("presence is not enough for a unified pair: CUE fills the "
                "missing half from the other side, so the two copies agree "
                "because one of them was written by the law rather than by "
                "the author"),
        "finding": "predicted by a second reader as the next hole, confirmed "
                   "by measurement before it was written",
        "generated_by": "controls/build.py",
    }
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if any control on disk differs from generated")
    args = ap.parse_args(argv)

    files = build()
    problems = []
    for name, doc in sorted(files.items()):
        target = HERE / name
        text = json.dumps(doc, indent=2) + "\n"
        if args.check:
            if not target.exists():
                problems.append(f"{name} is missing")
            elif target.read_text(encoding="utf-8") != text:
                problems.append(f"{name} differs from what build.py generates")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    if args.check:
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(f"{len(files)} control(s), {len(problems)} problem(s)")
        return 1 if problems else 0
    print(f"wrote {len(files)} control file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
