"""record.py: one implementation per rule, the way envelope.py is.

Written from CONTRACT.md §Dispatch The record, before the module
existed. Each test names the contract rule it pins:

  Rec1  fields land in the contracted order (input order is not enough)
  Rec2  status launched carries no result
  Rec3  model.ran is the stream value, or null with a note — never the alias
  Rec4  observed is the probe's result or {"unresolved": ...}, never false
  Rec5  lane is "dev"
  Rec6  validate names the field it refuses
  Rec7  follows is a list of ids; unit is a string
  Rec8  a record that fails validation is never "valid"
  Rec9  schema-complete: lineage, role, snapshot.mode, containment, status
"""

from __future__ import annotations

import unittest

import launch_support as ls


class _RecordCase(unittest.TestCase):
    def recmod(self):
        # Load inside the test method so a missing file is an empty
        # stub, not a setUp ERROR, and the assertion below is the red.
        if getattr(self, "record", None) is None:
            self.record = ls.require_module(self, "record")
        return self.record

    def built(self, **overrides):
        record = self.recmod()
        payload = dict(self._minimum())
        payload.update(overrides)
        try:
            return record.build(payload)
        except TypeError:
            return record.build(**payload)

    def _minimum(self):
        return {
            "id": "20260826T000000Z-plan-grok-abc123",
            "lane": "dev",
            "stage": "plan",
            "unit": "work",
            "lineage": {"branch": "work", "base_sha": "a" * 40},
            "follows": [],
            "job": "plan",
            "role": "read",
            "dispatched_by": ls.AGENT,
            "at": {"launched": "2026-08-26T00:00:00Z", "closed": None},
            "snapshot": {
                "mode": "whole",
                "ref_name": "HEAD",
                "ref_sha": "b" * 40,
                "behind_tip": 0,
                "root": "/tmp/jobs/id/snapshot",
            },
            "harness": {
                "name": "grok",
                "version": "1.0.5",
                "isolation": {
                    "mechanism": "home",
                    "env": {"GROK_HOME": "/tmp/jobs/id/home/grok"},
                    "home": "/tmp/jobs/id/home/grok",
                    "auth_files": ["auth.json"],
                    "store": "/tmp/jobs/id/home/grok/sessions",
                    "observed": {"unresolved": "behavioural probe has not run"},
                },
                "sandbox": "plan",
                "containment": "policy",
                "argv": ["grok", "-s", "uuid"],
            },
            "model": {
                "requested": "alias-requested",
                "effort_requested": None,
                "ran": None,
                "read_from": None,
            },
            "session": {
                "id": "00000000-0000-4000-8000-000000000001",
                "stream": None,
                "stream_sha256_at_close": None,
            },
            "brief": {
                "template": {"path": "ops/devlane/task/jobs.json", "sha256": "c" * 64},
                "scope": "pin",
                "inputs": [],
                "sha256": "d" * 64,
                "bytes": 3,
            },
            "caps": {"cap-out": 500000, "source": "wires.py"},
            "overrides": [],
            "attempts": [],
            "result": None,
            "status": "launched",
        }

    def _raises_naming(self, rec, field):
        with self.assertRaises(Exception) as caught:
            self.recmod().validate(rec)
        self.assertIn(field, str(caught.exception).lower())
        return caught.exception


class FieldOrderIsTheContract(_RecordCase):
    """Rec1 — a caller diffing two records sees the same fields in the
    same places. envelope.py already treats key order as shape.
    Input is deliberately reversed so an echo-the-payload stub fails."""

    def test_built_record_keys_match_the_contracted_order(self):
        payload = self._minimum()
        scrambled = {k: payload[k] for k in reversed(list(payload))}
        self.assertNotEqual(tuple(scrambled), ls.RECORD_FIELDS)
        record = self.recmod()
        try:
            rec = record.build(scrambled)
        except TypeError:
            rec = record.build(**scrambled)
        self.assertEqual(tuple(rec), ls.RECORD_FIELDS)

    def test_module_declares_the_same_order(self):
        declared = getattr(self.recmod(), "FIELDS", None)
        self.assertIsNotNone(declared, "FIELDS is the order contract")
        self.assertEqual(tuple(declared), ls.RECORD_FIELDS)


class LaunchedCarriesNoResult(_RecordCase):
    """Rec2 — written at launch with status: launched and no result."""

    def test_a_launched_record_has_result_none(self):
        rec = self.built(
            status="launched",
            result={
                "head": "b" * 40,
                "changed_paths": [],
                "residual_paths": [],
                "envelope": {},
            },
        )
        self.assertEqual(rec["status"], "launched")
        self.assertIsNone(rec["result"])

    def test_validate_refuses_a_launched_record_that_already_has_a_result(self):
        rec = self.built()
        rec["result"] = {
            "head": "b" * 40,
            "changed_paths": [],
            "residual_paths": [],
            "envelope": {},
        }
        rec["status"] = "launched"
        self._raises_naming(rec, "result")


class ModelRanIsNeverTheAliasCopiedOver(_RecordCase):
    """Rec3 — requested is the alias; ran is the stream; no stream →
    null with a note, never the alias copied over."""

    def test_ran_null_is_not_silently_filled_with_requested(self):
        rec = self.built()
        rec["model"]["ran"] = None
        rec["model"]["requested"] = "alias-requested"
        rec["model"]["read_from"] = None
        rec["model"].pop("note", None)
        rec.pop("note", None)
        rec = self.recmod().validate(rec)
        self.assertIsNone(rec["model"]["ran"])
        self.assertEqual(rec["model"]["requested"], "alias-requested")
        self.assertNotEqual(rec["model"]["ran"], rec["model"]["requested"])
        note = rec.get("note") or rec["model"].get("note")
        self.assertTrue(
            str(note or "").strip(),
            "null ran must carry a note, not silence",
        )

    def test_copying_the_alias_into_ran_without_a_stream_is_refused(self):
        rec = self.built()
        rec["model"]["ran"] = rec["model"]["requested"]
        rec["model"]["read_from"] = None
        rec["session"]["stream"] = None
        self._raises_naming(rec, "ran")


class ObservedIsNeverAManufacturedFalse(_RecordCase):
    """Rec4 / plan (u) — observed is verbatim or {"unresolved": why}."""

    def test_unresolved_is_the_honest_absent_observation(self):
        payload = self._minimum()
        del payload["harness"]["isolation"]["observed"]
        record = self.recmod()
        try:
            rec = record.build(payload)
        except TypeError:
            rec = record.build(**payload)
        isolation = rec["harness"]["isolation"]
        self.assertIn("observed", isolation)
        observed = isolation["observed"]
        self.assertIsInstance(observed, dict)
        self.assertIn("unresolved", observed)
        self.assertNotIn(observed.get("unresolved"), (None, "", False))

    def test_observed_false_is_refused(self):
        rec = self.built()
        rec["harness"]["isolation"]["observed"] = False
        self._raises_naming(rec, "observed")

    def test_observed_true_without_evidence_is_refused(self):
        rec = self.built()
        rec["harness"]["isolation"]["observed"] = True
        self._raises_naming(rec, "observed")


class LaneIsDev(_RecordCase):
    """Rec5 — every record is lane: "dev"; the launcher refuses main."""

    def test_built_lane_is_dev(self):
        rec = self.built(lane="prod")
        self.assertEqual(rec["lane"], "dev")

    def test_a_product_lane_is_refused(self):
        rec = self.built()
        rec["lane"] = "prod"
        self._raises_naming(rec, "lane")


class ValidateNamesTheField(_RecordCase):
    """Rec6 — a record that fails validation is never committed; the
    launcher exits non-zero naming the field."""

    def test_a_missing_field_is_named(self):
        rec = self.built()
        del rec["job"]
        self._raises_naming(rec, "job")


class FollowsAndUnitAreShaped(_RecordCase):
    """Rec7 / plan (q) — follows [ids], unit a string defaulting to the
    branch. Neither is context.prior."""

    def test_follows_is_a_list_and_unit_is_a_string(self):
        rec = self.built(
            follows=["20260826T000000Z-plan-grok-000001"],
            unit="",
        )
        self.assertIsInstance(rec["follows"], list)
        self.assertEqual(len(rec["follows"]), 1)
        self.assertIsInstance(rec["unit"], str)
        self.assertTrue(rec["unit"].strip(), "unit defaults to the lineage branch")
        self.assertEqual(rec["unit"], rec["lineage"]["branch"])

    def test_follows_default_is_an_empty_list_not_absent(self):
        payload = self._minimum()
        del payload["follows"]
        record = self.recmod()
        try:
            rec = record.build(payload)
        except TypeError:
            rec = record.build(**payload)
        self.assertIn("follows", rec)
        self.assertEqual(rec["follows"], [])


class StatusIsTheEnum(_RecordCase):
    """Rec8 — status is launched | closed | died."""

    def test_unknown_status_is_refused(self):
        rec = self.built()
        rec["status"] = "finished"
        self._raises_naming(rec, "status")


class SchemaIsComplete(_RecordCase):
    """Rec9 — a shallow validator that accepts lineage=None, role=execute,
    or snapshot.mode=shared-and-wrong is not the contract."""

    def test_lineage_none_is_refused(self):
        rec = self.built()
        rec["lineage"] = None
        self._raises_naming(rec, "lineage")

    def test_lineage_missing_branch_is_refused(self):
        rec = self.built()
        rec["lineage"] = {"base_sha": "a" * 40}
        self._raises_naming(rec, "lineage")

    def test_role_execute_is_refused(self):
        rec = self.built()
        rec["role"] = "execute"
        self._raises_naming(rec, "role")

    def test_snapshot_mode_shared_is_refused(self):
        rec = self.built()
        rec["snapshot"]["mode"] = "shared-and-wrong"
        self._raises_naming(rec, "mode")

    def test_containment_must_be_os_or_policy(self):
        rec = self.built()
        rec["harness"]["containment"] = "hope"
        self._raises_naming(rec, "containment")

    def test_follows_must_be_a_list(self):
        rec = self.built()
        rec["follows"] = "20260826T000000Z-plan-grok-000001"
        self._raises_naming(rec, "follows")
