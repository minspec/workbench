"""Grok summary.json head_commit is judged against snapshot.ref_sha.

Written from CONTRACT.md §Dispatch Isolation per harness, grok row:
``its head_commit is cross-checked against snapshot.ref_sha``.

Observed: records 20260828T213936Z-tests-grok-44a721,
20260828T213939Z-tests-grok-7f670a and 20260828T214309Z-tests-grok-dcddad
closed with envelope status invalid, note 'head_commit mismatch', while
each snapshot held one new commit on top of the ref and summary.json
head_commit equalled the ref.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import launch_support as ls


class GrokHeadCommitIsJudgedAgainstTheRef(ls._TempLaunch):
    """A write job that commits in its snapshot closes with head_commit
    judged against the ref, not against the post-write HEAD."""

    def test_a_write_job_that_commits_closes_with_head_commit_judged_against_the_ref(
            self):
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_HEAD_COMMIT"] = self.ref
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        code, out, err = self.dispatch(self.argv_for(
            job="author-tests", harness="grok", stage="tests",
            scope="pin grok head_commit against the ref",
        ))
        text = self.combined(out, err)
        self.assertNotEqual(
            code, ls.REFUSAL_EXIT,
            f"a write job that commits must not refuse: {text!r}",
        )
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed")

        result = rec.get("result") or {}
        ref_sha = rec["snapshot"]["ref_sha"]
        head = result.get("head")
        self.assertEqual(ref_sha, self.ref)
        self.assertTrue(head, "write collect records a head")
        self.assertNotEqual(
            head, ref_sha,
            "plant: the snapshot must hold a new commit on top of the ref",
        )
        snapshot = self.snapshot_of(rec)
        self._git(
            "merge-base", "--is-ancestor", ref_sha, head, repo=snapshot,
        )
        porcelain = self.porcelain(repo=snapshot)
        self.assertEqual(
            porcelain, "",
            f"plant: snapshot tree must be clean, got {porcelain!r}",
        )

        stream = rec.get("session", {}).get("stream") or ""
        self.assertTrue(stream, "a grok stream path is recorded")
        stream_path = Path(stream)
        self.assertTrue(stream_path.is_file(), f"stream missing: {stream}")
        self.assertEqual(
            stream_path.name, "summary.json",
            f"grok stream is summary.json, got {stream_path.name!r}",
        )
        summary = json.loads(stream_path.read_text(encoding="utf-8"))
        self.assertEqual(
            summary.get("head_commit"), ref_sha,
            "plant: summary.json head_commit must equal the ref",
        )
        self.assertNotEqual(
            summary.get("head_commit"), head,
            "plant: head_commit equals the ref, not the post-write HEAD",
        )

        envelope = result.get("envelope") or {}
        note = envelope.get("note")
        self.assertNotEqual(
            envelope.get("status"), "invalid",
            "head_commit equal to snapshot.ref_sha is not a mismatch on a "
            f"write that committed; envelope={envelope!r} rec={rec!r}",
        )
        self.assertNotIn(
            "head_commit mismatch",
            str(note or "").lower(),
            f"close must not name a mismatch when head_commit equals "
            f"the ref: note={note!r}",
        )
