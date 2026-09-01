#!/usr/bin/env python3
"""Run context.cue against the controls: one that must pass, four that must not.

    python3 ops/devlane/harness/vet_context.py

A schema with no document behind it is law nobody has read. Every
constraint in `context.cue` was written from an incident, and until this
existed none of them had ever been shown to fire -- which is the same
"nobody looked" that the schema is built to refuse.

Two halves, and the second is the one that gets skipped:

  the positive control   `controls/dispatchable.json` must VALIDATE. A
                         schema that rejects everything passes every
                         negative test perfectly.

  the negative controls  each `controls/rejects/*.json` must be
                         rejected AND rejected AT THE FIELD its
                         sidecar names. A document refused for some
                         other reason reports a guard that is not
                         there: measured 2026-08-23, the stale-version
                         fixture was rejected the whole time with
                         `mechanism: conflicting values`, because the
                         constraint had collapsed a disjunction rather
                         than firing.

Exits 0 when every control behaves, 1 when one does not, and 2 when
`cue` is absent -- refusing rather than reporting a pass it did not run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROLS = HERE / "controls"

#: BOTH files, always. Vetting context.cue alone left every helper
#: `#NonEmpty` undefined and reported a tidy count of failures that were
#: really "the schema did not load" -- an INVALID run wearing a result
#: (measured 2026-08-23).
SCHEMA = [str(HERE / "context.cue"), str(HERE / "evidence.cue")]

#: Which definition a control is judged against. The positives say so by
#: name; a reject's sidecar may override it.
POSITIVE = {"dispatchable-flags.json": "#Dispatchable",
            "dispatchable-home.json": "#Dispatchable",
            "evidence-receipt.json": "#Admissible"}


#: Paths that must be PRESENT in the source document, not merely valid
#: once the law has been applied. CUE cannot express this: a constraint
#: is also a DEFAULT, so `operator_config_present: false` in the gate
#: SUPPLIES that value to a document that never mentioned it, and
#: `cue vet -c` cannot tell — after inference the field is concrete.
#: Measured 2026-08-23: 28 paths of the flags control could be deleted
#: and the result still validated (Codex, PR #40 round three, three of
#: them; the rest came from deleting every leaf in turn).
#:
#: The rule for being on this list: omitting the field lets the gate
#: MANUFACTURE the clean answer. Not "the field is important".
MUST_BE_STATED = [
    "role",
    "harness.isolation.observed.operator_config_present",
    "harness.isolation.observed.harness_version",
    "staged.count",
    "staged.given",
    "staged.proof.withheld_present",
    "staged.proof.given_unmet",
    "task.produces",
    "task.report_fields",
    "unmet_requirements",
    "dangling_references",
]

#: Pairs the law unifies, which must ALSO be equal in the source. The
#: presence list is not enough for these: `task.produces` can be stated
#: as `[{"path": …}]` and CUE will copy the interface across from the
#: brief, so the document claims an agreement it never made. Comparing
#: the raw values kills the whole class in one rule, rather than
#: enumerating every leaf and going stale when a field is added
#: (predicted by a second reader as the next hole; confirmed by
#: measurement before it was written down).
MUST_MATCH_IN_SOURCE = [
    ("task.produces", "brief.deliverables"),
    ("task.report_fields", "brief.report_fields"),
]

#: Omittable and legitimately so, with the reason. A path that is
#: neither here nor above is a NEW hole, and `omittable()` reports it —
#: which is the only thing that stops this list going stale the next
#: time the law grows a constraint.
WAIVED = {
    "argv": "a seam that is never invoked has no argv (#Interface, optional)",
    "output": "same (#Interface, optional)",
    "fields": "same (#Interface, optional)",
    "declared_at": "carried by the brief's copy, which is required",
    "name": "carried by the brief's copy, which is required",
    "path": "carried by the brief's copy, which is required",
    "interface": "carried by the brief's copy, which is required",
}


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else key
            yield here
            yield from _leaves(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            here = f"{prefix}.{i}"
            yield here
            yield from _leaves(value, here)


def _at(doc, path):
    node = doc
    for step in path.split("."):
        node = node[int(step)] if step.isdigit() else node[step]
    return node


def _drop(doc, path):
    steps = path.split(".")
    node = doc
    for step in steps[:-1]:
        node = node[int(step)] if step.isdigit() else node[step]
    last = steps[-1]
    del node[int(last) if last.isdigit() else last]


def _present(doc, path):
    node = doc
    for step in path.split("."):
        if isinstance(node, list):
            if not step.isdigit() or int(step) >= len(node):
                return False
            node = node[int(step)]
        elif isinstance(node, dict):
            if step not in node:
                return False
            node = node[step]
        else:
            return False
    return True


def vet(document, definition):
    """(ok, output) from `cue vet` on one document."""
    proc = subprocess.run(
        ["cue", "vet", "-d", definition, *SCHEMA, str(document)],
        capture_output=True, text=True, check=False)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def check_stated():
    """Two halves, and the second is what stops this going stale.

    Every path on MUST_BE_STATED is present in each positive control —
    otherwise the control itself is relying on the gate to supply it.
    And every path that CAN be omitted while still validating is either
    on that list or explicitly waived; a path on neither is a new place
    where the law manufactures its own clean answer, and it is reported
    as a failure rather than discovered by the next reviewer.
    """
    problems = []
    for name, definition in sorted(POSITIVE.items()):
        if definition != "#Dispatchable":
            continue
        control = CONTROLS / name
        if not control.exists():
            continue
        doc = json.loads(control.read_text(encoding="utf-8"))
        for path in MUST_BE_STATED:
            if not _present(doc, path):
                problems.append(f"{name}: {path} is not stated")

        omittable = []
        for path in list(_leaves(doc)):
            candidate = json.loads(json.dumps(doc))
            try:
                _drop(candidate, path)
            except (KeyError, IndexError, TypeError):
                continue
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False) as handle:
                json.dump(candidate, handle)
                temporary = handle.name
            ok, _out = vet(temporary, "#Dispatchable")
            Path(temporary).unlink(missing_ok=True)
            if ok:
                omittable.append(path)

        unexplained = [p for p in omittable
                       if p not in MUST_BE_STATED
                       and p.rsplit(".", 1)[-1] not in WAIVED
                       and not p.rsplit(".", 1)[-1].isdigit()]
        for path in unexplained:
            problems.append(
                f"{name}: {path} can be omitted and still validate, and is "
                f"neither required to be stated nor waived")
        print(f"  {'pass' if not unexplained else 'FAIL'}  {name}: "
              f"{len(omittable)} omittable path(s), {len(unexplained)} "
              f"unexplained")
    return problems


def stated(document):
    """Paths a dispatchable context must state, and this one does not.

    The half `cue vet` cannot do, exported so a launcher can run it
    against a real context rather than only against the controls. An
    empty list means the document said everything the law would
    otherwise have said on its behalf.
    """
    doc = json.loads(Path(document).read_text(encoding="utf-8"))
    missing = [f"{path} is not stated" for path in MUST_BE_STATED
               if not _present(doc, path)]
    for left, right in MUST_MATCH_IN_SOURCE:
        if not (_present(doc, left) and _present(doc, right)):
            continue
        if _at(doc, left) != _at(doc, right):
            missing.append(
                f"{left} and {right} are unified by the law but differ in "
                f"the document, or one states less than the other")
    return missing


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--stated"]:
        if len(argv) != 2:
            print("usage: vet_context.py --stated <context.json>",
                  file=sys.stderr)
            return 2
        missing = stated(argv[1])
        for path in missing:
            print(f"  not stated: {path}", file=sys.stderr)
        print(f"{len(MUST_BE_STATED)} required path(s), "
              f"{len(missing)} omitted")
        return 1 if missing else 0

    if shutil.which("cue") is None:
        print("cue is not on PATH: refusing rather than reporting a pass",
              file=sys.stderr)
        return 2

    problems = []

    # One positive control per isolation mechanism: a rule that only
    # the home branch witnesses is a rule the flags branch never
    # exercises, and both branches now carry a refusal.
    problems += check_stated()
    positives = sorted(p for p in CONTROLS.glob("*.json")
                       if p.name in POSITIVE)
    missing = sorted(set(POSITIVE) - {p.name for p in positives})
    if missing:
        print(f"positive control(s) missing: {', '.join(missing)}",
              file=sys.stderr)
        return 1
    for positive in positives:
        definition = POSITIVE[positive.name]
        ok, out = vet(positive, definition)
        print(f"  {'pass' if ok else 'FAIL'}  {positive.name} must validate "
              f"as {definition}")
        if not ok:
            problems.append(f"{positive.name} no longer validates:\n{out}")

    rejects = sorted(p for p in (CONTROLS / "rejects").glob("*.json")
                     if not p.name.endswith(".reason.json"))
    if not rejects:
        # An empty fixture set satisfies "every negative control was
        # rejected" perfectly, which is the empty-snapshot defect these
        # very fixtures exist to pin.
        print("no negative controls found", file=sys.stderr)
        return 1

    for document in rejects:
        reason = json.loads(
            document.with_suffix(".reason.json").read_text(encoding="utf-8"))
        want = reason["expect_path"]
        if reason.get("checked_by") == "source":
            # `cue vet` ACCEPTS these -- that is the defect they
            # witness -- so the refusal comes from reading the document,
            # and the law must accept it, or the fixture would be
            # rejected for some other reason and isolate nothing.
            complaints = stated(document)
            valid, out = vet(document, reason.get("definition",
                                                  "#Dispatchable"))
            hit = [c for c in complaints if want in c]
            if not hit:
                problems.append(
                    f"{document.name}: nothing complained about {want}; "
                    f"the fixture witnesses nothing. Got: {complaints}")
                verdict = "FAIL no complaint"
            elif not valid:
                problems.append(
                    f"{document.name}: refused by the law for another "
                    f"reason, so it cannot isolate this:\n{out}")
                verdict = "FAIL not isolated"
            else:
                verdict = "pass (source)"
        else:
            ok, out = vet(document, reason.get("definition", "#Dispatchable"))
            if ok:
                problems.append(
                    f"{document.name} was ACCEPTED; it must not be")
                verdict = "FAIL accepted"
            elif want not in out:
                problems.append(
                    f"{document.name} was rejected, but not at {want}:\n{out}")
                verdict = f"FAIL wrong path (wanted {want})"
            else:
                verdict = "pass"
        print(f"  {verdict:<34} {document.name} — {reason['change']}")

    for problem in problems:
        print(f"\n{problem}", file=sys.stderr)
    print(f"\n{len(rejects) + len(positives)} control(s), "
          f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
