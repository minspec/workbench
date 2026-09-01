"""The dispatch record as a committed file, filled from a real launch.

Written from CONTRACT.md §Dispatch The record. Plan items (f)(g)(m)(o)(q).

  R1  field order, id form, lane, dispatched_by
  R2  follows and unit default and land
  R3  model.ran from fixture streams, or null with a note
  R4  brief --check re-derives; a planted digest fails
  R5  caps equal wires.py for the role
  R6  behind_tip is a count, not a judgement
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import launch_support as ls


class RecordShapeFromAClosedDispatch(ls._TempLaunch):
    """R1 — the file on disk is the contract's ordered record."""

    def test_closed_record_keys_are_the_contracted_order(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(tuple(rec), ls.RECORD_FIELDS)
        self.assertEqual(rec["lane"], "dev")
        self.assertEqual(rec["stage"], "plan")
        self.assertEqual(rec["job"], "plan")
        self.assertEqual(rec["role"], "read")
        self.assertEqual(rec["dispatched_by"], ls.AGENT)
        self.assertRegex(rec["id"], ls.ID_RE)
        self.assertEqual(rec["id"].split("-")[1], "plan")
        self.assertIn("grok", rec["id"])
        self.assertEqual(rec["lineage"]["branch"], "work")
        self.assertIsInstance(rec["at"], dict)
        self.assertIn("launched", rec["at"])
        self.assertIn("closed", rec["at"])
        self.assertTrue(rec["at"]["launched"])
        self.assertTrue(rec["at"]["closed"])
        snap = rec["snapshot"]
        self.assertEqual(snap["mode"], "whole")
        self.assertEqual(snap["ref_sha"], self.ref)
        self.assertEqual(self.the_job_dir().name, rec["id"])
        self.assertEqual(
            rec["harness"]["containment"], "policy",
        )

    def test_the_record_commit_is_on_the_lineage_branch(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(
            self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            rec["lineage"]["branch"],
        )
        self.assertEqual(rec["lineage"]["branch"], "work")
        self.assertIn(
            rec["id"],
            (self.repo / ".dev" / "records" / "dispatches" / f"{rec['id']}.json"
             ).name,
        )


class FollowsAndUnitLand(ls._TempLaunch):
    """R2 / plan (q). follows is the dispatch-graph edge, not
    context.prior. unit defaults to the lineage branch."""

    def test_omitted_follows_is_an_empty_list_and_unit_defaults_to_the_branch(
            self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["follows"], [])
        self.assertIsInstance(rec["follows"], list)
        self.assertEqual(rec["unit"], rec["lineage"]["branch"])
        self.assertEqual(rec["unit"], "work")

    def test_given_follows_and_unit_land_verbatim(self):
        prior = "20260826T000000Z-plan-grok-aaaaaa"
        rec, *_ = self.launch_ok(self.argv_for(
            job="plan", harness="grok", stage="plan", extra=[
                "--follows", prior, "--unit", "WO-7",
            ],
        ))
        self.assertEqual(rec["follows"], [prior])
        self.assertEqual(rec["unit"], "WO-7")
        self.assertNotEqual(rec["unit"], rec["lineage"]["branch"])
        # context.prior is not this field.
        self.assertNotIn("prior", rec)


class ModelRanComesFromTheStream(ls._TempLaunch):
    """R3 / plan (g) — requested is the alias; ran is the stream.

    The ran-model is baked into the fake CLI; TASK_LAUNCH_RAN_MODEL is
    not in the parent environment, so copying that env cannot satisfy
    these tests.
    """

    def _assert_stream_digest(self, rec):
        stream = rec["session"]["stream"]
        self.assertTrue(stream, "a stream path is recorded")
        path = Path(stream)
        self.assertTrue(path.is_file(), f"stream missing: {stream}")
        digest = ls.sha256_file(path)
        self.assertEqual(
            rec["session"]["stream_sha256_at_close"], digest,
        )
        self.assertNotIn("TASK_LAUNCH_RAN_MODEL", os.environ)

    def test_grok_ran_equals_the_fixture_stream_value_not_the_alias(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["model"]["requested"], ls.REQUESTED_MODEL)
        self.assertEqual(rec["model"]["ran"], ls.RAN_MODEL)
        self.assertNotEqual(rec["model"]["ran"], rec["model"]["requested"])
        self.assertTrue(rec["model"]["read_from"])
        self.assertIn("summary.json", rec["model"]["read_from"])
        self._assert_stream_digest(rec)

    def test_claude_ran_equals_the_fixture_stream_value(self):
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        self.assertEqual(rec["model"]["requested"], ls.REQUESTED_MODEL)
        self.assertEqual(rec["model"]["ran"], ls.RAN_MODEL)
        self.assertNotEqual(rec["model"]["ran"], rec["model"]["requested"])
        self.assertTrue(rec["model"]["read_from"])
        self._assert_stream_digest(rec)

    def test_codex_ran_equals_the_fixture_stream_value(self):
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        self.assertEqual(rec["model"]["requested"], ls.REQUESTED_MODEL)
        self.assertEqual(rec["model"]["ran"], ls.RAN_MODEL)
        self.assertNotEqual(rec["model"]["ran"], rec["model"]["requested"])
        self.assertTrue(rec["model"]["read_from"])
        named = (rec["session"]["stream"] or "") + (rec["model"]["read_from"] or "")
        self.assertIn("rollout", named)
        self._assert_stream_digest(rec)

    def test_no_stream_leaves_ran_null_with_a_note_never_the_alias(self):
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "0"
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertIsNone(rec["model"]["ran"])
        self.assertEqual(rec["model"]["requested"], ls.REQUESTED_MODEL)
        self.assertNotEqual(rec["model"]["ran"], rec["model"]["requested"])
        note = rec.get("note") or rec["model"].get("note") or json.dumps(rec)
        self.assertTrue(
            any(w in str(note).lower()
                for w in ("no stream", "null", "absent", "missing")),
            f"null ran must carry a note, not silence: {note!r}",
        )

    def test_grok_head_commit_mismatch_is_recorded_not_silent(self):
        bogus = "ab" * 20
        self.assertNotEqual(bogus, self.ref)
        os.environ["TASK_LAUNCH_HEAD_COMMIT"] = bogus
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        blob = json.dumps(rec).lower()
        self.assertIn("head_commit", blob)
        self.assertTrue(
            "mismatch" in blob or (rec.get("result") or {}).get("envelope", {})
            .get("status") == "invalid",
            f"grok head_commit ≠ ref_sha must be named, not accepted: {rec!r}",
        )
        self.assertNotEqual(rec["snapshot"]["ref_sha"], bogus)


class BriefCheckRederives(ls._TempLaunch):
    """R4 / plan (m) — brief --check re-renders from jobs.json@ref_sha
    + scope + input digests."""

    def test_brief_check_accepts_an_untouched_record(self):
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
            scope="check-the-brief",
        )
        code, out, err = self.run_main(["brief", "--check", rec["id"]])
        self.assertEqual(
            code, 0,
            f"brief --check must pass on the record just written: "
            f"{out}{err}",
        )

    def test_a_planted_wrong_digest_makes_brief_check_fail(self):
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
            scope="check-the-brief",
        )
        path = self.the_record_path()
        before = path.read_bytes()
        bogus = "0" * 64
        self.assertNotEqual(rec["brief"]["sha256"], bogus)
        ls.plant_bytes(
            path,
            lambda raw: raw.replace(
                rec["brief"]["sha256"].encode("utf-8"),
                bogus.encode("utf-8"),
            ),
            expect="edit",
            recognisable=lambda after: b'"brief"' in after,
        )
        after = path.read_bytes()
        self.assertNotEqual(after, before)
        self.assertIn(bogus.encode("utf-8"), after)
        self.assertNotIn(rec["brief"]["sha256"].encode("utf-8"), after)
        code, out, err = self.run_main(["brief", "--check", rec["id"]])
        self.assertNotEqual(code, 0, f"planted digest must fail: {out}{err}")

    def test_input_digest_enters_the_record(self):
        report = self.home / "in" / "given.md"
        body = b"# given input\n"
        self.plant_new_file(report, body, must_contain="given input")
        digest = hashlib.sha256(body).hexdigest()
        rec, *_ = self.launch_ok(self.argv_for(
            job="check-tests", harness="grok", stage="check-tests",
            extra=["--input", str(report)],
        ))
        inputs = rec["brief"]["inputs"]
        self.assertEqual(len(inputs), 1, "exactly one --input landed")
        self.assertEqual(inputs[0]["sha256"], digest)
        copied = self.the_job_dir() / "in" / "given.md"
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_bytes(), body)


class CapsEqualWires(ls._TempLaunch):
    """R5 / plan (o)."""

    def test_record_caps_equal_wires_budget_and_name_their_source(self):
        wires = ls.load_path(self, ls.WIRES_PATH, "task_launch_wires")
        expected = wires.budget()
        self.assertIsInstance(expected, int)
        self.assertGreater(expected, 0)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = rec["caps"]
        self.assertIsInstance(caps, dict)
        self.assertTrue(caps, "caps must not be an empty dict")
        source = str(caps.get("source", ""))
        self.assertIn("wires", source)
        values = []

        def walk(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, bool):
                return
            elif isinstance(obj, (int, float)) or (
                    isinstance(obj, str) and obj.isdigit()):
                values.append(int(obj))

        walk(caps)
        self.assertIn(
            expected, values,
            f"caps must carry wires.budget()={expected}, got {caps!r}",
        )


class BehindTipIsACount(ls._TempLaunch):
    """R6 — behind_tip is rev-list --count ref_sha..lineage."""

    def test_behind_tip_matches_rev_list_count(self):
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan", ref=self.mid_sha,
        )
        counted = int(self._git(
            "rev-list", "--count", f"{self.mid_sha}..work",
        ).stdout.strip())
        self.assertGreater(counted, 0)
        self.assertEqual(rec["snapshot"]["behind_tip"], counted)
        self.assertIsInstance(rec["snapshot"]["behind_tip"], int)
