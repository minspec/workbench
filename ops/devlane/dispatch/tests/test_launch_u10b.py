"""U10b red pins: D-ENV-4 and D-DIFF-1.

Authored from `.dev/docs/scratch/harness-fixes-ratification.md`
Amendment 2026-08-30 — U10b (ratified at 5f205a8), as amended
2026-08-30 (2) at b4746e6. launch.py was not read. Existing
collect/F1 cases that only assert `status == "invalid"` already
pass at HEAD; these tests pin the discriminating evidence.

  D-ENV-4  a post-run refusal amends the parsed envelope; findings,
           counts, artifacts and spend survive verbatim; stamp
           survives verbatim except stamp.ref, which the launcher
           normalises to the snapshot ref_sha
  D-DIFF-1 a review-shaped job with base_sha == ref_sha is refused
           before launch: no job directory, snapshot, record, or spend
"""

from __future__ import annotations

import json
import os

import launch_support as ls

NINE = (
    "job", "status", "verdict", "counts", "findings",
    "artifacts", "spend", "stamp", "note",
)

# Distinctive values that `_invalid(...)` / `_envelope(b"")` cannot
# coincidentally reproduce. Counts are deliberately not the length of
# the findings list so a recomputed tally is not "verbatim".
PLANTED_COUNTS = {"p1": 5, "p2": 3, "p3": 3, "opinions": 1}
PLANTED_FINDINGS = [
    {
        "severity": "p1",
        "where": "dispatch collect read-role-head",
        "claim": "D-ENV-4-survives-P1-a",
        "reproduce": "python3 -m unittest "
        "dev.app.dispatch.tests.test_launch_u10b",
    },
    {
        "severity": "p2",
        "where": "dispatch collect off-lineage-head",
        "claim": "D-ENV-4-survives-P2-b",
        "reproduce": None,
    },
]
PLANTED_ARTIFACTS = {"review": "out/review.json"}
PLANTED_SPEND = {"harness": "grok", "total": 7777, "out": 333, "runs": 4}
PLANTED_STAMP = {
    "ref": "u10b-stamp-ref",
    "started": "2026-08-30T05:04:26Z",
    "ended": "2026-08-30T05:19:00Z",
}
PLANTED_NOTE = "harness-produced-note-u10b"


def _planted(job):
    return {
        "job": job,
        "status": "ok",
        "verdict": "changes",
        "counts": dict(PLANTED_COUNTS),
        "findings": [dict(item) for item in PLANTED_FINDINGS],
        "artifacts": dict(PLANTED_ARTIFACTS),
        "spend": dict(PLANTED_SPEND),
        "stamp": dict(PLANTED_STAMP),
        "note": PLANTED_NOTE,
    }


class _U10bLaunch(ls._TempLaunch):
    def _emit(self, planted):
        body = json.dumps(planted)
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body
        return planted

    def _raw_object(self):
        raw = self.the_job_dir() / "raw.out"
        self.assertTrue(raw.is_file(), "raw.out is missing")
        data = raw.read_bytes()
        self.assertTrue(data, "raw.out is empty")
        text = data.decode("utf-8").strip()
        self.assertTrue(text, "raw.out is whitespace")
        emitted = json.loads(text)
        self.assertIsInstance(emitted, dict, f"raw.out is not an object: {text!r}")
        return emitted

    def _envelope_of(self, rec):
        result = rec.get("result") or {}
        env = result.get("envelope") if isinstance(result, dict) else None
        self.assertIsInstance(
            env, dict,
            f"record has no result.envelope: {rec!r}",
        )
        return env

    def _assert_plant_landed(self, emitted, planted):
        """The harness actually emitted the distinctive evidence."""
        self.assertGreater(
            len(planted["findings"]), 0,
            "plant must carry findings; empty evidence cannot discriminate",
        )
        self.assertGreater(
            planted["counts"]["p1"], 0,
            "plant must carry a non-zero p1 tally",
        )
        self.assertNotEqual(
            planted["spend"].get("total"), 0,
            "plant spend.total must not be the _invalid default of 0",
        )
        self.assertEqual(
            emitted.get("findings"), planted["findings"],
            "plant: raw.out findings did not land",
        )
        self.assertEqual(
            emitted.get("counts"), planted["counts"],
            "plant: raw.out counts did not land",
        )
        self.assertEqual(
            emitted.get("artifacts"), planted["artifacts"],
            "plant: raw.out artifacts did not land",
        )
        self.assertEqual(
            emitted.get("spend"), planted["spend"],
            "plant: raw.out spend did not land",
        )
        self.assertEqual(
            emitted.get("stamp"), planted["stamp"],
            "plant: raw.out stamp did not land",
        )
        self.assertEqual(emitted.get("status"), "ok", emitted)
        self.assertEqual(emitted.get("verdict"), "changes", emitted)
        self.assertEqual(emitted.get("note"), PLANTED_NOTE, emitted)

    def _assert_evidence_survived(self, env, planted, *, refusal, ref_sha):
        """D-ENV-4: only status, verdict and the note are amended.

        stamp.ref is the snapshot ref_sha, not the planted value —
        the launcher normalises it on every parsed envelope, refused
        or not (amendment 2026-08-30 (2) at b4746e6).
        """
        for key in NINE:
            self.assertIn(key, env, f"envelope missing {key}: {env!r}")
        self.assertEqual(
            env.get("status"), "invalid",
            f"post-run refusal amends status to invalid: {env!r}",
        )
        self.assertIsNone(
            env.get("verdict"),
            f"post-run refusal amends verdict to null: {env!r}",
        )
        findings = env.get("findings")
        self.assertIsInstance(findings, list, f"findings={findings!r}")
        self.assertEqual(
            len(findings), len(planted["findings"]),
            "findings must survive at the planted count, not be replaced "
            f"with an empty _invalid list: envelope={env!r}",
        )
        self.assertEqual(
            findings, planted["findings"],
            "findings must survive verbatim: "
            f"got {findings!r} planted {planted['findings']!r}",
        )
        self.assertEqual(
            env.get("counts"), planted["counts"],
            "counts must survive verbatim (not recomputed from findings): "
            f"got {env.get('counts')!r}",
        )
        self.assertEqual(
            env.get("artifacts"), planted["artifacts"],
            f"artifacts must survive verbatim: {env.get('artifacts')!r}",
        )
        self.assertEqual(
            env.get("spend"), planted["spend"],
            f"spend must survive verbatim: {env.get('spend')!r}",
        )
        planted_stamp = planted["stamp"]
        self.assertIsInstance(planted_stamp, dict, planted_stamp)
        self.assertTrue(ref_sha, "snapshot.ref_sha is required")
        self.assertNotEqual(
            planted_stamp.get("ref"), ref_sha,
            "plant: planted stamp.ref must differ from snapshot.ref_sha "
            "so a skipped normalisation is visible",
        )
        stamp = env.get("stamp")
        self.assertIsInstance(stamp, dict, f"stamp={stamp!r}")
        self.assertEqual(
            stamp.get("ref"), ref_sha,
            "stamp.ref is normalised to snapshot.ref_sha on the refusal "
            "path, same as the non-refused path; skipping that "
            "normalisation leaves the planted ref: "
            f"got {stamp.get('ref')!r} planted {planted_stamp.get('ref')!r} "
            f"ref_sha={ref_sha!r}",
        )
        self.assertNotEqual(
            stamp.get("ref"), planted_stamp.get("ref"),
            "normalisation must actually replace the planted stamp.ref",
        )
        self.assertEqual(
            {k: v for k, v in stamp.items() if k != "ref"},
            {k: v for k, v in planted_stamp.items() if k != "ref"},
            "stamp survives verbatim except stamp.ref: "
            f"got {stamp!r} planted {planted_stamp!r}",
        )
        self.assertEqual(
            env.get("job"), planted["job"],
            f"job is not in the amended set: {env.get('job')!r}",
        )
        note = str(env.get("note") or "")
        self.assertTrue(note, f"amended note must name the refusal: {env!r}")
        self.assertIn(
            refusal.lower(), note.lower(),
            f"note must name {refusal!r}: {note!r}",
        )
        self.assertNotEqual(
            note, planted["note"],
            "the note is amended to name the refusal; the harness note "
            f"alone is not that amendment: {note!r}",
        )


class ReadRoleHeadRefusalKeepsTheParsedEnvelope(_U10bLaunch):
    """D-ENV-4 / read-role-head: a read role that commits is refused
    after the harness has run, and the parsed envelope is retained."""

    def test_read_role_head_refusal_keeps_findings_counts_artifacts_spend_stamp(
            self):
        planted = self._emit(_planted("plan"))
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_HEAD_COMMIT"] = self.ref
        _code, _out, _err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
        ))
        rec = self.read_record()
        self.assertEqual(rec["role"], "read")
        result = rec["result"]
        ref_sha = rec["snapshot"]["ref_sha"]
        self.assertEqual(ref_sha, self.ref)
        self.assertNotEqual(
            result.get("head"), ref_sha,
            "plant: the read-role worker committed on top of the ref",
        )
        changed = ls.changed_entries(result.get("changed_paths"))
        self.assertTrue(
            any("worker.py" in str(item) for item in changed),
            f"plant: worker.py is in changed_paths, got {changed!r}",
        )
        emitted = self._raw_object()
        self._assert_plant_landed(emitted, planted)
        env = self._envelope_of(rec)
        self._assert_evidence_survived(
            env, planted, refusal="read-role-head", ref_sha=ref_sha,
        )


class OffLineageHeadRefusalKeepsTheParsedEnvelope(_U10bLaunch):
    """D-ENV-4 / off-lineage-head: a write role whose HEAD does not
    descend from ref_sha is refused after the harness has run, and the
    parsed envelope is retained."""

    def test_off_lineage_head_refusal_keeps_findings_counts_artifacts_spend_stamp(
            self):
        planted = self._emit(_planted("implement"))
        os.environ["TASK_LAUNCH_ORPHAN"] = "1"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        _code, _out, _err = self.dispatch(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        rec = self.read_record()
        self.assertEqual(rec["role"], "write")
        ref_sha = rec["snapshot"]["ref_sha"]
        self.assertEqual(ref_sha, self.ref)
        envelope_blob = json.dumps((rec.get("result") or {}).get("envelope") or {})
        self.assertIn(
            "off-lineage-head",
            (self.combined(_out, _err) + json.dumps(rec) + envelope_blob).lower(),
            "plant: off-lineage-head is the refusal that fired",
        )
        emitted = self._raw_object()
        self._assert_plant_landed(emitted, planted)
        env = self._envelope_of(rec)
        self._assert_evidence_survived(
            env, planted, refusal="off-lineage-head", ref_sha=ref_sha,
        )


class UnparsedStdoutStillUsesInvalid(_U10bLaunch):
    """D-ENV-4 neighbour: when no envelope could be parsed, the
    existing `_invalid(...)` construction stands."""

    def test_prose_stdout_plus_orphan_head_is_still_the_invalid_construction(
            self):
        os.environ["TASK_LAUNCH_STDOUT"] = "prose"
        os.environ["TASK_LAUNCH_ORPHAN"] = "1"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        _code, _out, _err = self.dispatch(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        rec = self.read_record()
        raw = self.the_job_dir() / "raw.out"
        self.assertTrue(raw.is_file(), "harness ran; raw.out is missing")
        data = raw.read_bytes()
        self.assertTrue(data, "plant: prose stdout landed")
        self.assertNotIn(b"{", data, "plant: prose has no JSON object")
        env = self._envelope_of(rec)
        self.assertEqual(env.get("status"), "invalid", env)
        self.assertIsNone(env.get("verdict"), env)
        findings = env.get("findings")
        self.assertIsInstance(findings, list, f"findings={findings!r}")
        self.assertEqual(
            findings, [],
            "unparsed stdout must not invent the planted findings: "
            f"envelope={env!r}",
        )
        claims = [item.get("claim") for item in findings if isinstance(item, dict)]
        self.assertNotIn("D-ENV-4-survives-P1-a", claims)
        note = str(env.get("note") or "")
        self.assertTrue(note, f"invalid construction must name why: {env!r}")
        self.assertIn(
            "off-lineage-head", note.lower(),
            "the envelope note itself must name the off-lineage-head "
            "refusal; a parse-only note that drops the refusal is the "
            f"loss this pins: note={note!r} envelope={env!r}",
        )


class ReviewWithNothingToCompareIsRefusedBeforeLaunch(_U10bLaunch):
    """D-DIFF-1: base_sha == ref_sha is refused before the harness
    starts. The rejected alternative — record a no-comparison marker
    and run anyway — is pinned by the absence of a record."""

    def _plant_equal_base_and_ref(self):
        self._git("branch", "dev", self.ref)
        base = self._git("merge-base", "work", "dev").stdout.strip()
        self.assertEqual(
            base, self.ref,
            "plant: merge-base(work, dev) must equal --ref so the "
            f"comparison is empty; base={base} ref={self.ref}",
        )
        self.assertEqual(
            self._git("rev-parse", "work").stdout.strip(), self.ref,
        )
        self.assertEqual(
            self._git("rev-parse", "dev").stdout.strip(), self.ref,
        )
        return base

    def test_review_with_equal_base_and_ref_is_refused_before_launch(self):
        base = self._plant_equal_base_and_ref()
        self.assertEqual(self.job_dirs(), [], "jobs root starts empty")
        self.assertEqual(self.record_files(), [])
        before_head = self._git("rev-parse", "HEAD").stdout.strip()
        code, out, err = self.dispatch(self.argv_for(
            job="adversarial-review", harness="grok", stage="review",
            ref=self.ref,
            scope="pin empty comparison refusal",
        ))
        if code != ls.REFUSAL_EXIT:
            dirs = self.job_dirs()
            self.assertEqual(
                len(dirs), 1,
                "plant: HEAD still launches the empty-comparison review: "
                f"code={code} dirs={dirs} out={self.combined(out, err)!r}",
            )
            diff_path = dirs[0] / "diff.patch"
            self.assertTrue(
                diff_path.is_file(),
                f"plant: {{diff}} is written: {list(dirs[0].iterdir())}",
            )
            body = diff_path.read_text(encoding="utf-8")
            self.assertFalse(
                body.strip(),
                "plant: base_sha == ref_sha produces an empty comparison, "
                f"got {body!r}",
            )
            rec = self.read_record()
            lineage = rec.get("lineage") if isinstance(rec.get("lineage"), dict) else {}
            snap = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
            self.assertEqual(
                lineage.get("base_sha"), snap.get("ref_sha"),
                "plant: resolved base_sha equals ref_sha: "
                f"lineage={lineage!r} snapshot={snap!r}",
            )
            self.assertEqual(snap.get("ref_sha"), self.ref)
        text = self.assert_refusal(
            code, out, err, ident="empty-comparison",
            phrases=["work", self.ref, "empty"],
        )
        lower = text.lower()
        self.assertIn(base.lower(), lower)
        self.assert_not_started()
        self.assertEqual(
            self.job_dirs(), [],
            "D-DIFF-1 refuses before a job directory is minted: "
            f"{self.job_dirs()}",
        )
        snaps = list(self.jobs_root.rglob("snapshot"))
        self.assertEqual(snaps, [], f"no snapshot is minted: {snaps}")
        self.assertEqual(
            self.record_files(), [],
            "rejected alternative: a no-comparison marker must not be "
            f"recorded; records={self.record_files()}",
        )
        self.assertEqual(
            self._git("rev-parse", "HEAD").stdout.strip(), before_head,
            "a pre-launch refusal must not commit a record",
        )


class ReviewWithARealComparisonStillLaunches(_U10bLaunch):
    """D-DIFF-1 silent neighbour: a review whose base is not the ref
    still launches."""

    def test_review_with_a_non_empty_comparison_still_launches(self):
        self._git("branch", "dev", self.mid_sha)
        self.assertNotEqual(self.mid_sha, self.ref)
        base = self._git("merge-base", "work", "dev").stdout.strip()
        self.assertEqual(base, self.mid_sha)
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="adversarial-review", harness="grok", stage="review",
            scope="pin non-empty comparison neighbour",
        ))
        self.assertEqual(rec.get("status"), "closed")
        self.assertTrue(witness["argv"], "harness started")
        self.assertEqual(len(self.job_dirs()), 1)
        self.assertEqual(len(self.record_files()), 1)


class NonReviewJobWithEqualBaseAndRefIsNotThisRefusal(_U10bLaunch):
    """D-DIFF-1 silent neighbour: a plan job is not review-shaped, even
    when the lineage merge-base equals --ref."""

    def test_a_plan_job_with_equal_base_and_ref_still_launches(self):
        self._git("branch", "dev", self.ref)
        base = self._git("merge-base", "work", "dev").stdout.strip()
        self.assertEqual(base, self.ref, "plant: base_sha would equal ref_sha")
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="plan", harness="grok", stage="plan",
            ref=self.ref,
            scope="pin non-review neighbour of empty comparison",
        ))
        self.assertEqual(rec.get("job"), "plan")
        self.assertEqual(rec.get("status"), "closed")
        self.assertTrue(witness["argv"], "plan is not refused by D-DIFF-1")
        self.assertEqual(len(self.record_files()), 1)


class ReviewWithEmptyDiffButUnequalShasStillLaunches(_U10bLaunch):
    """D-DIFF-1 silent neighbour: the trigger is base_sha == ref_sha,
    not empty comparison text. An --allow-empty commit on the tip
    gives base != ref with a zero-byte diff and still launches."""

    def test_review_with_empty_diff_but_unequal_base_and_ref_still_launches(self):
        before = self.ref
        self._git("commit", "--allow-empty", "-m", "u10b-empty-on-tip")
        empty_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(
            empty_sha, before,
            "plant: --allow-empty must move HEAD off the previous tip",
        )
        self.assertEqual(
            self._git("rev-parse", "work").stdout.strip(), empty_sha,
        )
        self._git("branch", "dev", before)
        base = self._git("merge-base", "work", "dev").stdout.strip()
        self.assertEqual(base, before, "plant: merge-base is the previous tip")
        self.assertNotEqual(
            base, empty_sha,
            "plant: base_sha != ref_sha; the equal-sha refusal must not fire",
        )
        comparison = self._git("diff", base, empty_sha).stdout
        self.assertFalse(
            comparison.strip(),
            "plant: the comparison is empty even though the SHAs differ: "
            f"got {comparison!r}",
        )
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="adversarial-review", harness="grok", stage="review",
            ref=empty_sha,
            scope="pin empty-diff unequal-sha neighbour",
        ))
        self.assertEqual(rec.get("status"), "closed")
        self.assertTrue(witness["argv"], "harness started")
        self.assertEqual(len(self.job_dirs()), 1)
        self.assertEqual(len(self.record_files()), 1)
        snap = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
        self.assertEqual(
            snap.get("ref_sha"), empty_sha,
            f"snapshot.ref_sha is the empty commit: snapshot={snap!r}",
        )
        prompt = witness.get("prompt_text") or ""
        if not prompt:
            prompt = (self.the_job_dir() / "prompt.txt").read_text(
                encoding="utf-8",
            )
        self.assertIn(empty_sha, prompt, f"{{ref}} is the empty commit: {prompt!r}")
        self.assertIn(
            before, prompt,
            "{base} is the previous tip, so the comparison SHAs differ: "
            f"prompt={prompt!r}",
        )
        self.assertNotEqual(empty_sha, before)
        diff_path = self.the_job_dir() / "diff.patch"
        self.assertTrue(
            diff_path.is_file(),
            f"{{diff}} is written: {list(self.the_job_dir().iterdir())}",
        )
        body = diff_path.read_text(encoding="utf-8")
        self.assertFalse(
            body.strip(),
            "an empty comparison with unequal SHAs still launches: "
            f"got {body!r}",
        )
