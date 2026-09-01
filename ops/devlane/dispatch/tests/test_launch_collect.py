"""Collect: ancestry, changed_paths, residual_paths, refs/dispatch.

Written from CONTRACT.md §Dispatch Collect. Plan items (r)(s).

  C1  worker HEAD that does not descend from ref_sha → invalid, no fetch
  C2  committed-and-clean → changed_paths from ref_sha..head, residual empty
  C3  edited-only → changed_paths empty, residual non-empty
  C4  read role: head equals ref_sha; residual recorded not judged
"""

from __future__ import annotations

import json
import os

import launch_support as ls


class OffLineageHeadIsInvalidAndNotFetched(ls._TempLaunch):
    """C1 / plan (r) / contract off-lineage-head."""

    def test_an_orphan_head_is_invalid_and_does_not_create_refs_dispatch(self):
        os.environ["TASK_LAUNCH_ORPHAN"] = "1"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        before_refs = self.refs_map()
        _code, out, err = self.dispatch(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        rec = self.read_record()
        blob = (self.combined(out, err) + json.dumps(rec)).lower()
        envelope = (rec.get("result") or {}).get("envelope") or {}
        self.assertEqual(envelope.get("status"), "invalid")
        self.assertIn("off-lineage-head", blob)
        after_refs = self.refs_map()
        dispatch_refs = [
            n for n in after_refs if n.startswith("refs/dispatch/")
        ]
        self.assertEqual(
            dispatch_refs, [],
            "off-lineage-head must not fetch refs/dispatch",
        )
        extra = {
            n for n in (set(after_refs) - set(before_refs))
            if n.startswith("refs/dispatch/")
        }
        self.assertEqual(extra, set())
        self.assertNotIn(f"refs/dispatch/{rec['id']}", after_refs)


class CommittedCleanVersusEditedOnly(ls._TempLaunch):
    """C2 / C3 / plan (s)."""

    def test_a_worker_that_committed_and_left_a_clean_tree(self):
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec["result"]
        self.assertIsInstance(result, dict)
        changed = ls.changed_entries(result.get("changed_paths"))
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertGreater(
            len(changed), 0,
            "committed work produces a non-empty ref_sha..head name-only diff",
        )
        self.assertIn("worker.py", " ".join(changed))
        self.assertEqual(
            residual, [],
            f"a clean snapshot tree has empty residual_paths, got {residual!r}",
        )
        self.assertEqual(
            self.porcelain(repo=self.snapshot_of(rec)), "",
        )
        self.assertTrue(result.get("head"))
        self.assertNotEqual(result["head"], rec["snapshot"]["ref_sha"])
        # Ancestry: head descends from ref_sha. _git raises on non-zero,
        # so returning is the proof.
        self._git(
            "merge-base", "--is-ancestor",
            rec["snapshot"]["ref_sha"], result["head"],
            repo=self.snapshot_of(rec),
        )
        self.assertEqual(
            self.refs_map()[f"refs/dispatch/{rec['id']}"], result["head"],
        )

    def test_a_worker_that_only_edited_leaves_residuals_and_no_changed_paths(
            self):
        os.environ["TASK_LAUNCH_EDIT"] = "scratch.txt"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec["result"]
        changed = ls.changed_entries(result.get("changed_paths"))
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertEqual(
            changed, [],
            f"no commit means empty changed_paths, got {changed!r}",
        )
        self.assertGreater(
            len(residual), 0,
            "an uncommitted edit is residual_paths, not changed_paths",
        )
        self.assertTrue(
            any("scratch.txt" in str(item) for item in residual),
            residual,
        )
        self.assertEqual(result.get("head"), rec["snapshot"]["ref_sha"])
        self.assertNotIn(
            f"refs/dispatch/{rec['id']}",
            # a write role with no new commit may still fetch HEAD==ref;
            # the pin is changed_paths empty. Fetching the same sha is
            # allowed; fetching something else is not.
            [n for n, sha in self.refs_map().items()
             if sha != rec["snapshot"]["ref_sha"] and n.startswith("refs/dispatch/")],
        )


class ReadRoleHeadEqualsRef(ls._TempLaunch):
    """C4 — read roles: head must equal ref_sha; residual is recorded
    not judged."""

    def test_a_read_role_records_head_equal_to_ref_sha(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        result = rec["result"]
        self.assertEqual(result["head"], rec["snapshot"]["ref_sha"])
        self.assertEqual(result["head"], self.ref)
        self.assertNotIn(f"refs/dispatch/{rec['id']}", self.refs_map())

    def test_a_read_role_with_an_uncommitted_edit_records_residual_not_invalid(
            self):
        os.environ["TASK_LAUNCH_EDIT"] = "notes.txt"
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        result = rec["result"]
        self.assertEqual(result["head"], rec["snapshot"]["ref_sha"])
        residual = ls.residual_entries(result.get("residual_paths"))
        self.assertGreater(len(residual), 0)
        envelope = result.get("envelope") or {}
        self.assertNotEqual(envelope.get("status"), "invalid")
        self.assertEqual(rec["status"], "closed")
