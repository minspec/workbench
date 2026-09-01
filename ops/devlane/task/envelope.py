#!/usr/bin/env python3
"""The typed envelope every task returns, and the rules it enforces.

`CONTRACT.md` §The envelope states the shape; this module is the only
place that builds one, so the shape cannot vary by caller. The rules
below are not validation for its own sake — each one closes a way the
caller could be misled while reading nothing but this dict:

**A task that could not look must not approve** (§Statuses). `status`
routes and `verdict` judges, and only a task that actually ran — status
`ok` — may carry `approve`. `invalid` carries no verdict at all, and
`tripped` may report what it found but never that the job is clean.

**Counts are derived, never asserted.** A tally accepted from a caller
is a second copy of the findings list, and two copies drift. `build`
computes it, and `validate` refuses an envelope whose tally has stopped
matching its findings — which is what a truncated list from a harness
looks like from the outside.

**A finding with no reproduction is an opinion, and says so.** The
`reproduce` key is always present; absence is `None` in the data rather
than a key the caller has to notice is missing. `counts["opinions"]` is
that same fact where routing can see it.

**Artifacts are handles, not contents.** The property this whole app
exists for is that iteration N costs the caller about what iteration 1
cost, and the way that breaks is prose migrating into the envelope. A
handle is one line and shorter than HANDLE_MAX; anything else is
content, and content belongs in the file the handle names.
"""

from __future__ import annotations

#: Key order is part of the shape: a caller diffing two envelopes, or a
#: reviewer reading one, sees the same fields in the same places.
FIELDS = ("job", "status", "verdict", "counts", "findings",
          "artifacts", "spend", "stamp", "note")
FINDING_FIELDS = ("severity", "where", "claim", "reproduce")
SPEND_FIELDS = ("harness", "total", "out", "runs")
STAMP_FIELDS = ("ref", "started", "ended")

STATUSES = ("ok", "invalid", "tripped")
VERDICTS = ("approve", "changes")
SEVERITIES = ("p1", "p2", "p3")

#: Longer than any path this lane produces, far shorter than a review.
HANDLE_MAX = 512

#: The wire shape supplied to structured-output capable harnesses.  This is
#: deliberately beside ``FIELDS`` and the constructor: callers do not keep a
#: second, gradually diverging description of an envelope.
ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "job": {"type": "string"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "verdict": {
            "type": ["string", "null"],
            "enum": [*VERDICTS, None],
        },
        "counts": {
            "type": "object",
            "properties": {
                key: {"type": "integer", "minimum": 0}
                for key in (*SEVERITIES, "opinions")
            },
            "required": [*SEVERITIES, "opinions"],
            "additionalProperties": False,
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "where": {"type": "string"},
                    "claim": {"type": "string"},
                    "reproduce": {"type": ["string", "null"]},
                },
                "required": list(FINDING_FIELDS),
                "additionalProperties": False,
            },
        },
        "artifacts": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "spend": {"type": "object"},
        "stamp": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "started": {"type": ["string", "null"]},
                "ended": {"type": ["string", "null"]},
            },
            "required": ["ref"],
            "additionalProperties": False,
        },
        "note": {"type": ["string", "null"]},
        "commit": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        },
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}


class EnvelopeError(ValueError):
    """The envelope would have misled the caller. Refuse, never repair."""


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise EnvelopeError(f"{field} must be a non-empty string")
    return " ".join(value.split())


def _handle(name, value):
    if not isinstance(value, str):
        raise EnvelopeError(f"artifacts[{name!r}] must be a path string")
    if not value.strip():
        raise EnvelopeError(f"artifacts[{name!r}] is empty")
    if len(value) > HANDLE_MAX:
        raise EnvelopeError(
            f"artifacts[{name!r}] is {len(value)} chars: a handle, not "
            f"contents, is the contract (max {HANDLE_MAX})")
    if "\n" in value or "\r" in value:
        raise EnvelopeError(
            f"artifacts[{name!r}] spans lines: that is the work product, "
            "not a handle to it")
    return value


def finding(severity, where, claim, reproduce=None):
    """One finding, normalized. A blank reproduction is an absent one."""
    if severity not in SEVERITIES:
        raise EnvelopeError(
            f"severity {severity!r} is not one of {list(SEVERITIES)}")
    if isinstance(reproduce, str) and not reproduce.strip():
        reproduce = None
    if reproduce is not None and not isinstance(reproduce, str):
        raise EnvelopeError("reproduce must be a command string or None")
    return {"severity": severity,
            "where": _text(where, "where"),
            "claim": _text(claim, "claim"),
            "reproduce": reproduce.strip() if reproduce else None}


def tally(findings):
    counts = dict.fromkeys(SEVERITIES, 0)
    counts["opinions"] = sum(1 for f in findings if not f.get("reproduce"))
    for f in findings:
        counts[f["severity"]] += 1
    return counts


def _spend(given):
    spend = {"harness": None, "total": 0, "out": 0, "runs": 0}
    for key, value in (given or {}).items():
        if key not in SPEND_FIELDS:
            raise EnvelopeError(f"spend has no field {key!r}")
        spend[key] = value
    if spend["harness"] is not None and not isinstance(spend["harness"], str):
        raise EnvelopeError("spend.harness must be a harness name or None")
    for key in ("total", "out", "runs"):
        value = spend[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise EnvelopeError(f"spend.{key} must be an integer")
        if value < 0:
            # worth.py prices from these; a negative subtracts from a
            # real cost somewhere else in the ledger.
            raise EnvelopeError(f"spend.{key} is negative")
    return spend


def _stamp(given):
    given = given or {}
    for key in given:
        if key not in STAMP_FIELDS:
            raise EnvelopeError(f"stamp has no field {key!r}")
    # Without a ref the envelope names no state, and a fact with no
    # state behind it cannot be re-checked by anyone, including its
    # author tomorrow.
    ref = given.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise EnvelopeError("stamp.ref must name the ref the task read")
    return {"ref": ref.strip(),
            "started": given.get("started"),
            "ended": given.get("ended")}


def build(job, *, status, verdict=None, findings=(), artifacts=None,
          spend=None, stamp=None, note=None):
    """Assemble an envelope, refusing any combination that would lie.

    Every rule about the assembled shape lives in `validate`, which
    this returns through — deliberately, and once. Checking a rule here
    as well would leave two implementations of it, and a suite that
    plants a fault in either one still passes on the other. Measured:
    the approve gate was written in both places and BOTH copies could
    be disabled undetected, because each was covering for the other.
    """
    normalized = [
        f if isinstance(f, dict) and tuple(f) == FINDING_FIELDS
        else finding(**dict(zip(FINDING_FIELDS, (
            f.get("severity"), f.get("where"), f.get("claim"),
            f.get("reproduce")), strict=True)))
        for f in findings]
    env = {
        "job": _text(job, "job"),
        "status": status,
        "verdict": verdict,
        "counts": tally(normalized),
        "findings": normalized,
        "artifacts": {name: _handle(name, value)
                      for name, value in (artifacts or {}).items()},
        "spend": _spend(spend),
        "stamp": _stamp(stamp),
        "note": " ".join(note.split()) if note else None,
    }
    return validate(env)


def invalid(job, note, *, stamp=None, spend=None, artifacts=None):
    """The task could not do its job. `note` says which way, and must.

    The note is required by `validate`, not re-checked here: see
    `build` on why one rule gets exactly one implementation.
    """
    return build(job, status="invalid", verdict=None, stamp=stamp,
                 spend=spend, artifacts=artifacts, note=note)


#: Container fields whose type must be proved before anything iterates
#: or indexes them. `bool` is not accepted for a dict or list by
#: isinstance, so no special case is needed here.
CONTAINER_TYPES = {
    "findings": list,
    "counts": dict,
    "artifacts": dict,
    "spend": dict,
    "stamp": dict,
}


def validate(env):
    """Re-check a built or parsed envelope. Returns it, or raises."""
    if not isinstance(env, dict):
        raise EnvelopeError("an envelope is a dict")
    if tuple(env) != FIELDS:
        raise EnvelopeError(
            f"envelope fields {list(env)} are not the contract's "
            f"{list(FIELDS)}")
    # A worker's JSON arrives here unvalidated, and every check below
    # either iterates or indexes one of these. A wrong type has to raise
    # EnvelopeError -- which the parser turns into an `invalid` envelope
    # -- rather than a TypeError, which escapes the parser and kills the
    # run instead of reporting it (Codex, PR #49: `"findings": null`
    # raised TypeError at the findings loop). Typed here rather than at
    # the one field reported, because the same hole is under every
    # container the contract names.
    for field, want in CONTAINER_TYPES.items():
        if not isinstance(env[field], want):
            raise EnvelopeError(
                f"{field} is {type(env[field]).__name__}, not "
                f"{want.__name__}")
    for f in env["findings"]:
        if not isinstance(f, dict):
            raise EnvelopeError(
                f"a finding is {type(f).__name__}, not dict")
    if env["status"] not in STATUSES:
        raise EnvelopeError(f"status {env['status']!r} is not a status")
    if env["verdict"] is not None and env["verdict"] not in VERDICTS:
        raise EnvelopeError(f"verdict {env['verdict']!r} is not a verdict")
    if env["verdict"] == "approve" and env["status"] != "ok":
        raise EnvelopeError(
            f"a {env['status']!r} task did not do its job and cannot "
            "approve one")
    if env["status"] == "invalid" and env["verdict"] is not None:
        raise EnvelopeError("an invalid task carries no verdict")
    if env["status"] == "invalid" and not env["note"]:
        raise EnvelopeError(
            "an invalid envelope must say why it could not look")
    for f in env["findings"]:
        if tuple(f) != FINDING_FIELDS:
            raise EnvelopeError(
                f"finding fields {list(f)} are not {list(FINDING_FIELDS)}")
        # `finding` is the one place a finding's rules live; re-running
        # it is how they get checked here without being restated here.
        if finding(**f) != f:
            raise EnvelopeError(f"finding {f} is not in normal form")
    if env["counts"] != tally(env["findings"]):
        # The list is what the task found; the tally is a copy of it.
        # When they disagree the list is short, which is exactly what a
        # truncated harness reply looks like from out here.
        raise EnvelopeError(
            f"counts {env['counts']} disagree with the findings "
            f"{tally(env['findings'])}: the list is not the tally")
    for name, value in env["artifacts"].items():
        _handle(name, value)
    _spend(env["spend"])
    _stamp(env["stamp"])
    return env
