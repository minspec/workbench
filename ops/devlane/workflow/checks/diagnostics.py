#!/usr/bin/env python3
"""A diagnostics snapshot, as a registered check (PLAN §9).

"Diagnostics snapshots recorded as `receipt.check` receipts (class
`diagnostics`) — same evidence shape, **no new machinery**." So there is no
verb here and no event type: this is an argv template in a gate-kind spec,
and `wf check` records its output exactly like any other check.

It is deliberately producer-agnostic. Whatever emits the diagnostics —
`cargo check --message-format=json`, a language server, a linter — this reads
JSON objects, one per line, and counts them by severity:

    diagnostics.py [--fail-on error] -- <producer command…>

Recognised shapes, because producers disagree and guessing wrong silently
reports zero problems:

    {"severity": "error", "file": …, "line": …, "message": …}
    {"level":    "error", …}                       (rustc / cargo)
    {"message": {"level": "error", …}}             (cargo --message-format=json)

Two producers do not speak in lines at all, and are read as whole
documents BEFORE the line walk — a SARIF log read line-by-line is
every line unparsed and no findings, which is a silent pass over a
file full of errors:

    {"$schema": …sarif…, "runs": [{"results": [...]}]}   SARIF 2.1.0
    <testsuites><testsuite><testcase>…                    JUnit XML

Each document format carries a state that is NOT "clean", and saying
so is the point of reading them at all:

    SARIF  "runs": []            no tool ran   (an empty `results` IS clean)
    JUnit  no <testcase> at all  no test ran   (.dev/process/tdd.md: "NO
                                               TESTS RAN" is not a red —
                                               and not a pass either)

A line that is not JSON is counted as unparsed and reported. Exits 1 when
anything at or above --fail-on is present, 0 otherwise — and prints the
counts either way, because a check that says only "ok" cannot be told from
one that read nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from typing import NamedTuple

SEVERITY_ORDER = ["note", "help", "info", "information", "warning", "error"]
ALIASES = {"warn": "warning", "information": "info", "err": "error"}


def rank(severity: str) -> int:
    severity = ALIASES.get(severity.lower(), severity.lower())
    return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else -1


def severity_of(record):
    """Pull a severity out of whichever shape the producer used."""
    if not isinstance(record, dict):
        return None
    for key in ("severity", "level"):
        value = record.get(key)
        if isinstance(value, str):
            return ALIASES.get(value.lower(), value.lower())
        if isinstance(value, int):  # LSP numeric severities: 1=error … 4=hint
            return {1: "error", 2: "warning", 3: "info", 4: "note"}.get(value)
    nested = record.get("message")
    if isinstance(nested, dict):
        return severity_of(nested)
    return None


class Reading(NamedTuple):
    """What one producer's output amounted to.

    `refusal` is the field that keeps this check honest: it is set when
    the producer emitted a well-formed document proving it never did
    the work, which no count can express — zero findings and zero work
    look identical in `counts`.
    """

    counts: dict
    unparsed: int
    detail: str | None = None
    refusal: str | None = None


#: SARIF 2.1.0 §3.27.10. A result with no `level` resolves through its
#: rule's defaultConfiguration before falling back to `warning` — NOT
#: to "unparsed", which would report a valid document as unreadable.
SARIF_DEFAULT = "warning"

def _prolog_declares_entities(text):
    """Does the PROLOG carry a DOCTYPE or ENTITY declaration?

    Walked structurally rather than regex-bounded. The first version
    took "the prolog" to be everything before the first `<` followed by
    a name character — but an XML COMMENT may legally contain such text,
    and `<!-- <A --><!DOCTYPE x [<!ENTITY e "v">]>` then truncated the
    prolog before the declaration, admitted the document, and expanded
    the entity. That defeated the whole mitigation, including the one
    cited to justify the S314 exemption in ruff.toml (Codex, PR #38).

    So: skip whitespace, comments and processing instructions the way a
    parser does, and answer on the first thing that is none of those.
    """
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n or text[i] != "<":
            return False
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            if end == -1:
                return False        # unterminated; the parser will refuse
            i = end + 3
            continue
        if text.startswith("<?", i):
            end = text.find("?>", i + 2)
            if end == -1:
                return False
            i = end + 2
            continue
        # Whatever this is, the prolog ends here: either a declaration
        # (the thing we refuse) or an element start (the thing that ends
        # the prolog).
        return text.startswith("<!DOCTYPE", i) or text.startswith("<!ENTITY", i)
    return False


def _component_rules(run, item):
    """The rules of the component a result's rule reference names.

    SARIF §3.52: a reportingDescriptorReference may carry a
    toolComponent index into `run.tool.extensions`. Reading only the
    driver's rules gave an extension rule configured `error` the global
    `warning` fallback, so the gate passed a document that declared a
    failure (Codex, PR #38).
    """
    tool = run.get("tool") or {}
    reference = item.get("rule") if isinstance(item.get("rule"), dict) else {}
    component = reference.get("toolComponent")
    if isinstance(component, dict):
        index = component.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            extensions = tool.get("extensions") or []
            if 0 <= index < len(extensions) and isinstance(
                    extensions[index], dict):
                return [r for r in (extensions[index].get("rules") or [])
                        if isinstance(r, dict)]
    driver = tool.get("driver") or {}
    return [r for r in (driver.get("rules") or []) if isinstance(r, dict)]


def _local(tag):
    """The tag without its namespace: "{uri}testcase" -> "testcase"."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _rule_level(item, rules):
    """A result may name its rule three ways; all reach the default.

    `ruleId`, `ruleIndex`, and a `rule` reportingDescriptorReference
    carrying either. Resolving only `ruleId` scored a rule that
    defaults to `error` as a warning and passed the gate (Codex, PR
    review of 3453dc1).
    """
    def configured(rule):
        return (rule.get("defaultConfiguration") or {}).get("level")

    ref = item.get("rule") if isinstance(item.get("rule"), dict) else {}
    index = item.get("ruleIndex")
    if index is None:
        index = ref.get("index")
    if isinstance(index, int) and not isinstance(index, bool) \
            and 0 <= index < len(rules):
        level = configured(rules[index])
        if level:
            return level
    identifier = item.get("ruleId") or ref.get("id")
    if identifier:
        for rule in rules:
            if rule.get("id") == identifier:
                level = configured(rule)
                if level:
                    return level
    return None


def sarif_reading(text: str):
    """Read a SARIF log, or return None if this is not one."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        doc = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    # §3.13.2: `version` is mandatory and its value is fixed. Claiming
    # any object with a list-valued `runs` swallowed ordinary producer
    # records — {"severity":"error","runs":[{}]} passed the gate
    # silently (Codex, PR review of 3453dc1).
    schema = str(doc.get("$schema") or "").lower()
    if doc.get("version") != "2.1.0" and "sarif" not in schema:
        return None
    runs = doc.get("runs")
    if runs is None:
        return Reading({}, 0, refusal="sarif: `runs` is null")
    if not isinstance(runs, list):
        return None
    if not runs:
        # §3.13.4 permits an empty `runs` for a producer with no run
        # data — a result-management query matching nothing, say. For a
        # GATE the producer's job is to analyse code, so zero runs is
        # zero analysis and refusing is right; `executionSuccessful`
        # below is the spec's own signal and now carries the weight.
        return Reading({}, 0, refusal="sarif: no runs in the log")
    counts = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        for invocation in run.get("invocations") or []:
            # §3.20.14: the tool itself reporting that it did not
            # complete. No count can express that.
            if isinstance(invocation, dict) \
                    and invocation.get("executionSuccessful") is False:
                return Reading(
                    {}, 0,
                    refusal="sarif: an invocation reports "
                            "executionSuccessful=false")
        results = run.get("results")
        if results is None:
            # §3.14.23: absent or null `results` means the tool failed
            # to populate the log, which is not "found nothing".
            return Reading({}, 0, refusal="sarif: a run has no `results`")
        for item in results:
            if not isinstance(item, dict):
                continue
            # §3.27.9: a result whose `kind` is anything but "fail" is
            # not a finding at all, whatever level it carries.
            if item.get("kind", "fail") != "fail":
                continue
            level = (item.get("level")
                     or _rule_level(item, _component_rules(run, item))
                     or SARIF_DEFAULT)
            if not isinstance(level, str) or level == "none":
                continue
            level = ALIASES.get(level.lower(), level.lower())
            counts[level] = counts.get(level, 0) + 1
    return Reading(counts, 0)


def junit_reading(text: str):
    """Read a JUnit report, or return None if this is not one."""
    stripped = text.strip()
    if "<testsuite" not in stripped:
        return None
    if _prolog_declares_entities(stripped):
        # ElementTree is not hardened against entity expansion and
        # defusedxml is not available in a stdlib-only lane. Only the
        # PROLOG is inspected: a declaration can appear nowhere else,
        # while the body routinely carries the same characters inside
        # CDATA — Maven Surefire writes captured stdout that way, so
        # scanning the whole document failed any test that printed
        # HTML (Codex, PR review of 3453dc1).
        return Reading(
            {}, 0,
            refusal="junit: the prolog declares XML entities; refusing")
    try:
        root = ElementTree.fromstring(stripped)
    except ElementTree.ParseError:
        # Malformed XML is not a JUnit report we can read. Fall through
        # to the line walk, which will count it as unparsed and say so.
        return None
    if _local(root.tag) not in ("testsuites", "testsuite"):
        return None
    # Namespace-insensitive throughout: a default xmlns turns every tag
    # into "{uri}testsuites", the reader declined the document, and a
    # report carrying real failures went out as unparsed lines and exit
    # 0 — a silent pass, the worst shape available (Grok, PR review of
    # 3453dc1). cargo-nextest emits no xmlns; other producers do.
    cases = [e for e in root.iter() if _local(e.tag) == "testcase"]
    if not cases:
        # cargo-nextest omits skipped tests by default (nextest#885), so
        # an all-skipped run is also a testcase-free report — after the
        # tool genuinely ran. The two are indistinguishable in this
        # output, so the refusal names both rather than asserting one.
        return Reading(
            {}, 0,
            refusal="junit: no test cases in the report — nothing ran, or "
                    "everything was skipped and the producer omitted them")
    failures = errors = skipped = 0
    for testcase in cases:
        # A failed assertion and a test that never reached one are
        # different facts. Both fail the gate; the report keeps them
        # apart because .dev/process/tdd.md's honest-red rule is
        # exactly this distinction.
        for child in testcase:
            kind = _local(child.tag)
            failures += kind == "failure"
            errors += kind == "error"
            skipped += kind == "skipped"
    counts = {}
    if failures + errors:
        counts["error"] = failures + errors
    if skipped:
        counts["note"] = skipped
    detail = (f"junit: {len(cases)} test(s), {failures} failure(s), "
              f"{errors} error(s), {skipped} skipped")
    return Reading(counts, 0, detail=detail)


def summarise(stdout: str, stderr: str = "") -> Reading:
    """Read one producer's two streams.

    The streams are kept APART for the document readers and joined only
    for the line walk. A producer emits its document on one stream and
    its progress chatter on the other — `cargo clippy
    --message-format=sarif` writes the log to stdout while cargo writes
    "Compiling foo v0.1.0" to stderr — so concatenating them first
    turned a valid log full of errors into unparsed lines and a clean
    exit (Codex, PR review of 3453dc1).
    """
    for stream in (stdout, stderr):
        if not stream.strip():
            continue
        for reader in (sarif_reading, junit_reading):
            reading = reader(stream)
            if reading is not None:
                return reading
    text = stdout + "\n" + stderr
    counts, unparsed = {}, 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            unparsed += 1
            continue
        severity = severity_of(record)
        if severity is None:
            unparsed += 1
            continue
        counts[severity] = counts.get(severity, 0) + 1
    return Reading(counts, unparsed)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    producer = None
    if "--" in argv:
        index = argv.index("--")
        argv, producer = argv[:index], argv[index + 1:]

    parser = argparse.ArgumentParser(description="diagnostics snapshot")
    parser.add_argument("--fail-on", default="error",
                        help="lowest severity that fails (default: error)")
    args = parser.parse_args(argv)

    if not producer:
        print("diagnostics: no producer command given", file=sys.stderr)
        print("usage: diagnostics.py [--fail-on error] -- <command…>", file=sys.stderr)
        return 64

    try:
        proc = subprocess.run(producer, capture_output=True, text=True,
                              errors="replace", check=False)
    except (FileNotFoundError, PermissionError) as exc:
        # Not "no diagnostics". A producer that cannot run has told us
        # nothing, and reporting that as clean is the failure this whole
        # repository exists to prevent.
        print(f"diagnostics: the producer could not run: {exc}", file=sys.stderr)
        return 1

    reading = summarise(proc.stdout, proc.stderr)
    threshold = rank(args.fail_on)
    if threshold < 0:
        print(f"diagnostics: unknown --fail-on severity {args.fail_on!r}",
              file=sys.stderr)
        return 64

    counts = reading.counts
    failing = sum(n for sev, n in counts.items() if rank(sev) >= threshold)
    shown = ", ".join(f"{sev}={n}" for sev, n in sorted(counts.items())) or "none"
    print(f"diagnostics: {shown}"
          f"{f', unparsed={reading.unparsed}' if reading.unparsed else ''}"
          f" (producer exited {proc.returncode}, failing at >= {args.fail_on})")
    if reading.detail:
        print(f"  {reading.detail}")
    for line in (proc.stdout + proc.stderr).splitlines()[:20]:
        if line.strip():
            print(f"  {line[:160]}")
    if reading.refusal:
        # A well-formed document proving the work never happened. No
        # count can say this: zero findings and zero work are the same
        # empty dict, and only one of them is clean.
        print(f"diagnostics: {reading.refusal} — refusing to score that"
              f" as clean", file=sys.stderr)
        return 1
    if failing:
        return 1
    if proc.returncode != 0:
        # No failing diagnostic was parsed, yet the producer itself failed —
        # a broken manifest, a bad flag, a crash before speaking JSON. That
        # is not a clean tree; it is a producer that told us nothing, and
        # scoring it clean records a satisfying receipt for a failed check.
        print(f"diagnostics: producer exited {proc.returncode} with no"
              f" recognized failing diagnostic — failing the check",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
