"""The envelope: the shape a caller routes on without reading the work.

Written from `ops/devlane/task/CONTRACT.md` §The envelope and §Statuses,
before the module existed. Each test names the contract rule it pins:

  R1  a task that could not look must not return `approve`
  R2  a finding with no reproduction is an opinion, and the envelope
      says so rather than hiding it
  R3  artifacts are handles, not contents
  R4  every task returns the same shape
  R5  spend is present so worth.py can price a job
  R6  the stamp carries the ref the task actually read
  R7  counts are derived, never asserted — a supplied count that
      disagrees with the findings is a truncated list, not a tally
"""

from __future__ import annotations

import json
import unittest

import support

envelope = support.load("envelope")

STAMP = {"ref": "f0b8bb3", "started": "2026-08-22T12:00:00Z",
         "ended": "2026-08-22T12:04:00Z"}


def a_finding(severity="p2", reproduce="python3 -m unittest x"):
    return envelope.finding(
        severity, "run.py §launch", "the battery is never armed",
        reproduce=reproduce)


class TheRefusalRule(unittest.TestCase):
    """R1 — status routes; verdict judges; only a task that ran may approve."""

    def test_an_ok_task_may_approve(self):
        env = envelope.build("verify", status="ok", verdict="approve",
                             stamp=STAMP)
        self.assertEqual(env["verdict"], "approve")

    def test_an_invalid_task_may_not_approve(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="invalid", verdict="approve",
                           stamp=STAMP, note="ref absent")

    def test_a_tripped_task_may_not_approve(self):
        # A partial deliverable has not seen the whole job, so it has
        # no standing to approve it.
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("sweep", status="tripped", verdict="approve",
                           stamp=STAMP, note="cap wire tripped")

    def test_a_tripped_task_may_still_report_changes(self):
        env = envelope.build("sweep", status="tripped", verdict="changes",
                             findings=[a_finding("p1")], stamp=STAMP,
                             note="cap wire tripped")
        self.assertEqual((env["status"], env["verdict"]),
                         ("tripped", "changes"))

    def test_invalid_carries_no_verdict_at_all(self):
        env = envelope.invalid("verify", "harness missing", stamp=STAMP)
        self.assertIsNone(env["verdict"])
        self.assertEqual(env["status"], "invalid")

    def test_an_invalid_task_may_not_report_changes_either(self):
        # `invalid` means the task could not look. Not-looking produces
        # no judgement at all, not a milder one.
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="invalid", verdict="changes",
                           stamp=STAMP, note="snapshot failed")

    def test_a_finding_smuggled_past_its_constructor_is_refused(self):
        env = envelope.build("sweep", status="ok", verdict="changes",
                             findings=[a_finding("p1")], stamp=STAMP)
        env["findings"][0]["severity"] = "p4"
        env["counts"] = dict.fromkeys(("p1", "p2", "p3", "opinions"), 0)
        with self.assertRaises(envelope.EnvelopeError):
            envelope.validate(env)

    def test_invalid_must_say_why(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.invalid("verify", "", stamp=STAMP)

    def test_an_unknown_status_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="done", stamp=STAMP)

    def test_an_unknown_verdict_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="ok", verdict="lgtm", stamp=STAMP)


class AFindingWithoutAReproductionSaysSo(unittest.TestCase):
    """R2 — the absence is a value in the data, not a missing key."""

    def test_the_key_is_present_and_null(self):
        env = envelope.build("adversarial-review", status="ok",
                             verdict="changes",
                             findings=[a_finding(reproduce=None)],
                             stamp=STAMP)
        self.assertIn("reproduce", env["findings"][0])
        self.assertIsNone(env["findings"][0]["reproduce"])

    def test_blank_whitespace_is_absence_not_a_command(self):
        env = envelope.build("adversarial-review", status="ok",
                             verdict="changes",
                             findings=[a_finding(reproduce="   ")],
                             stamp=STAMP)
        self.assertIsNone(env["findings"][0]["reproduce"])

    def test_the_envelope_counts_the_opinions(self):
        env = envelope.build(
            "adversarial-review", status="ok", verdict="changes",
            findings=[a_finding(reproduce=None), a_finding()], stamp=STAMP)
        self.assertEqual(env["counts"]["opinions"], 1)

    def test_a_finding_keeps_the_contract_key_order(self):
        # Same reason the top-level key set is ordered: two envelopes
        # from two runs are read side by side, and a field that moves
        # costs the reader the diff.
        env = envelope.build("sweep", status="ok", verdict="changes",
                             findings=[a_finding("p1")], stamp=STAMP)
        f = env["findings"][0]
        env["findings"][0] = {"claim": f["claim"], "severity": f["severity"],
                              "where": f["where"],
                              "reproduce": f["reproduce"]}
        with self.assertRaises(envelope.EnvelopeError):
            envelope.validate(env)

    def test_a_finding_needs_a_severity_a_where_and_a_claim(self):
        for kwargs in ({"severity": "p4"}, {"where": ""}, {"claim": ""}):
            with self.subTest(**kwargs), self.assertRaises(
                    envelope.EnvelopeError):
                envelope.finding(**{
                    "severity": "p2", "where": "f.py §s",
                    "claim": "c", **kwargs})


class ArtifactsAreHandles(unittest.TestCase):
    """R3 — the prose stays on disk; the caller reads it if it decides to."""

    def test_a_path_is_accepted(self):
        env = envelope.build("verify", status="ok", verdict="approve",
                             artifacts={"raw": "/tmp/t/raw.txt"}, stamp=STAMP)
        self.assertEqual(env["artifacts"]["raw"], "/tmp/t/raw.txt")

    def test_inlined_prose_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="ok", verdict="approve",
                           artifacts={"raw": "VERDICT: APPROVE\nFINDINGS:\n"},
                           stamp=STAMP)

    def test_a_handle_longer_than_a_path_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="ok", verdict="approve",
                           artifacts={"raw": "x" * (envelope.HANDLE_MAX + 1)},
                           stamp=STAMP)

    def test_an_empty_handle_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="ok", verdict="approve",
                           artifacts={"raw": ""}, stamp=STAMP)


class EverySoTaskReturnsTheSameShape(unittest.TestCase):
    """R4/R5/R6 — one key set, spend always priced, stamp always pinned."""

    def test_the_key_set_is_fixed_and_ordered(self):
        env = envelope.build("verify", status="ok", verdict="approve",
                             stamp=STAMP)
        self.assertEqual(list(env), list(envelope.FIELDS))

    def test_an_unknown_top_level_key_is_refused(self):
        env = envelope.build("verify", status="ok", verdict="approve",
                             stamp=STAMP)
        env["transcript"] = "...the whole conversation..."
        with self.assertRaises(envelope.EnvelopeError):
            envelope.validate(env)

    def test_spend_is_present_even_when_nothing_was_spent(self):
        env = envelope.build("verify", status="ok", verdict="approve",
                             stamp=STAMP)
        self.assertEqual(env["spend"],
                         {"harness": None, "total": 0, "out": 0, "runs": 0})

    def test_negative_spend_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="ok", verdict="approve",
                           spend={"harness": "grok", "total": -1, "out": 0,
                                  "runs": 1},
                           stamp=STAMP)

    def test_the_stamp_names_the_ref_that_was_read(self):
        env = envelope.build("verify", status="ok", verdict="approve",
                             stamp=STAMP)
        self.assertEqual(env["stamp"]["ref"], "f0b8bb3")

    def test_a_stamp_without_a_ref_is_refused(self):
        with self.assertRaises(envelope.EnvelopeError):
            envelope.build("verify", status="ok", verdict="approve",
                           stamp={"started": STAMP["started"],
                                  "ended": STAMP["ended"]})

    def test_it_round_trips_through_json_unchanged(self):
        env = envelope.build("adversarial-review", status="ok",
                             verdict="changes", findings=[a_finding("p1")],
                             artifacts={"diff": "/tmp/t/d.patch"},
                             spend={"harness": "stub", "total": 9, "out": 3,
                                    "runs": 1},
                             stamp=STAMP)
        self.assertEqual(json.loads(json.dumps(env)), env)


class CountsAreDerivedNeverAsserted(unittest.TestCase):
    """R7 — a tally that can disagree with the list is a tally that lies."""

    def test_build_tallies_the_findings_itself(self):
        env = envelope.build(
            "sweep", status="ok", verdict="changes",
            findings=[a_finding("p1"), a_finding("p3"), a_finding("p3")],
            stamp=STAMP)
        self.assertEqual(
            env["counts"],
            {"p1": 1, "p2": 0, "p3": 2, "opinions": 0})

    def test_validate_refuses_a_tally_that_stopped_matching(self):
        env = envelope.build("sweep", status="ok", verdict="changes",
                             findings=[a_finding("p1")], stamp=STAMP)
        env["counts"]["p1"] = 4
        with self.assertRaises(envelope.EnvelopeError):
            envelope.validate(env)


class AMalformedEnvelopeIsInvalidNotACrash(unittest.TestCase):
    """Worker output is untrusted, so a wrong TYPE must refuse, not raise.

    `_candidate_envelope` in run.py catches EnvelopeError only. Anything
    else escapes the parser and kills the run, which turns "the worker
    replied badly" into "the runner fell over" -- the opposite of the
    contract's rule that unparseable output becomes an `invalid`
    envelope. Verified before the fix: `findings: None` raised
    TypeError: 'NoneType' object is not iterable (Codex, PR #49).
    """

    def _valid(self):
        return envelope.build("sweep", status="ok", verdict="changes",
                              findings=[a_finding("p1")], stamp=STAMP)

    def test_every_container_field_refuses_a_wrong_type(self):
        for field in ("findings", "counts", "artifacts", "spend", "stamp"):
            with self.subTest(field=field):
                env = self._valid()
                env[field] = None
                # the plant must be in the fixture before the assertion
                self.assertIsNone(env[field], "INVALID fixture: not planted")
                with self.assertRaises(envelope.EnvelopeError):
                    envelope.validate(env)

    def test_a_finding_that_is_not_a_dict_refuses(self):
        env = self._valid()
        env["findings"] = [3]
        self.assertEqual(env["findings"], [3], "INVALID fixture: not planted")
        with self.assertRaises(envelope.EnvelopeError):
            envelope.validate(env)

    def test_the_valid_envelope_these_cases_corrupt_still_passes(self):
        """The positive control: without the plant, validate accepts."""
        self.assertIsNotNone(envelope.validate(self._valid()))


if __name__ == "__main__":
    unittest.main()
