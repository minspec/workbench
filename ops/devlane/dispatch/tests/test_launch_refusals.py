"""Refusals: planted and fired, each with its silent neighbour.

Written from CONTRACT.md §Dispatch Refusals. Exit 3. The text of each
names expected / found / what would satisfy. ``stale-base`` is the only
overridable one.

  D1  identity — before any directory exists
  D2  live-target
  D3  ref
  D4  record-target (detached, dev, main, other branch, close-time)
  D5  stale-base + neighbours + the single override
  D6  model (missing; effort on codex)
  D7  write-role-unadmitted
  D8  scope-cap (1025 / 1024 / adjudicate)
  D9  history-vs-withheld, mode-unavailable
  D10 isolation
  D11 invalid overrides
"""

from __future__ import annotations

import json
import os
import stat

import launch_support as ls


class IdentityRefusesBeforeAnyDirectory(ls._TempLaunch):
    """D1 / contract identity / plan (f)."""

    def test_unset_wf_agent_refuses_and_creates_no_job_dir(self):
        os.environ.pop("WF_AGENT", None)
        before = list(self.jobs_root.iterdir())
        self.assertEqual(before, [], "jobs root starts empty")
        # Unwritable jobs root: mkdir-then-rmdir cannot hide a mint.
        writable = self.jobs_root.stat().st_mode
        os.chmod(
            self.jobs_root,
            writable & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH),
        )
        try:
            code, out, err = self.dispatch()
        finally:
            os.chmod(self.jobs_root, writable)
        self.assert_refusal(
            code, out, err, ident="identity",
            phrases=["identity", "Name <address>", "WF_AGENT"],
        )
        self.assertEqual(
            list(self.jobs_root.iterdir()), before,
            "identity refuses before any directory is made",
        )
        self.assert_not_started()
        self.assertEqual(self.record_files(), [])

    def test_a_bare_name_is_refused_the_same_way(self):
        os.environ["WF_AGENT"] = "Grok"
        before = list(self.jobs_root.iterdir())
        code, out, err = self.dispatch()
        text = self.assert_refusal(
            code, out, err, ident="identity",
            phrases=["identity", "Name <address>", "Grok"],
        )
        self.assertIn("found", text.lower())
        self.assertEqual(list(self.jobs_root.iterdir()), before)
        self.assert_not_started()

    def test_the_name_addr_form_is_the_silent_neighbour(self):
        os.environ["WF_AGENT"] = ls.AGENT
        rec, witness, _out, _err = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        self.assertEqual(rec["dispatched_by"], ls.AGENT)
        self.assertTrue(witness["argv"])


class LiveTargetRefusesAJobsRootInsideTheRepo(ls._TempLaunch):
    """D2 / contract live-target."""

    def test_a_jobs_root_inside_the_invoking_repo_is_refused(self):
        inside = self.repo / "inside-jobs"
        inside.mkdir()
        os.environ["DISPATCH_JOBS"] = str(inside)
        listed = self.worktree_paths()
        self.assertTrue(
            any(str(self.repo.resolve()) == str(p) or
                str(self.repo.resolve()) in str(p)
                for p in listed),
            "plant: invoking repo is a worktree list entry",
        )
        code, out, err = self.dispatch()
        self.assert_refusal(
            code, out, err, ident="live-target",
            phrases=["live-target", "worktree", "DISPATCH_JOBS"],
        )
        self.assert_not_started()
        snaps = list(inside.rglob("snapshot"))
        self.assertEqual(snaps, [], "no snapshot is minted inside the repo")

    def test_a_jobs_root_outside_every_worktree_is_the_silent_neighbour(self):
        rec, witness, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = self.snapshot_of(rec)
        for wt in self.worktree_paths():
            resolved = os.path.realpath(wt)
            self.assertFalse(
                os.path.commonpath([os.path.realpath(snap), resolved])
                == resolved,
                f"snapshot {snap} is inside worktree {wt}",
            )
        self.assertTrue(witness["argv"])


class RefMustNameACommit(ls._TempLaunch):
    """D3 / contract ref."""

    def test_an_unresolvable_ref_is_refused_and_never_launches(self):
        missing = "no-such-ref-7e1c9a3d"
        code, out, err = self.dispatch(
            self.argv_for(ref=missing, stage="plan"),
        )
        self.assert_refusal(
            code, out, err, ident="ref",
            phrases=["ref", missing, "commit"],
        )
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])

    def test_a_resolvable_ref_is_the_silent_neighbour(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan",
                                 ref=self.ref)
        self.assertEqual(rec["snapshot"]["ref_sha"], self.ref)


class RecordTargetMustBeTheLineageBranch(ls._TempLaunch):
    """D4 / contract record-target / plan (f)."""

    def test_detached_head_is_refused(self):
        self._git("checkout", "--detach", self.ref)
        code, out, err = self.dispatch(self.argv_for(stage="plan"))
        self.assert_refusal(
            code, out, err, ident="record-target",
            phrases=["record-target", "detached"],
        )
        self.assertEqual(self.job_dirs(), [])
        self.assert_not_started()

    def test_a_checkout_on_dev_is_refused(self):
        self._git("checkout", "-b", "dev")
        code, out, err = self.dispatch(
            self.argv_for(lineage="dev", stage="plan"),
        )
        self.assert_refusal(
            code, out, err, ident="record-target",
            phrases=["record-target", "dev"],
        )
        self.assertEqual(self.job_dirs(), [])
        self.assert_not_started()

    def test_a_checkout_on_main_is_refused(self):
        self._git("checkout", "-b", "main")
        code, out, err = self.dispatch(
            self.argv_for(lineage="main", stage="plan"),
        )
        self.assert_refusal(
            code, out, err, ident="record-target",
            phrases=["record-target", "main"],
        )
        self.assertEqual(self.job_dirs(), [])
        self.assert_not_started()

    def test_a_branch_other_than_lineage_is_refused_naming_both(self):
        self._git("checkout", "-b", "other")
        code, out, err = self.dispatch(
            self.argv_for(lineage="work", stage="plan"),
        )
        text = self.assert_refusal(
            code, out, err, ident="record-target",
            phrases=["record-target", "work", "other"],
        )
        self.assertIn("work", text)
        self.assertIn("other", text)
        self.assertEqual(self.job_dirs(), [])
        self.assert_not_started()

    def test_close_after_the_checkout_moves_branch_refuses_and_leaves_the_file(
            self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        job_id = rec["id"]
        # Re-open the close path: plant a launched record for a new id
        # by copying the closed one, then switch branch and close.
        launched_id = "20260826T000000Z-plan-grok-d1ed01"
        src = self.the_record_path()
        dest = src.parent / f"{launched_id}.json"
        body = src.read_text(encoding="utf-8")
        self.assertIn(job_id, body)
        data = json.loads(src.read_text(encoding="utf-8"))
        data["id"] = launched_id
        data["status"] = "launched"
        data["result"] = None
        data["at"]["closed"] = None
        dest.write_text(json.dumps(data) + "\n", encoding="utf-8")
        landed = dest.read_text(encoding="utf-8")
        self.assertIn(launched_id, landed)
        self.assertIn('"launched"', landed)
        self.assertNotEqual(landed, src.read_text(encoding="utf-8"))

        job_dir = self.jobs_root / launched_id
        job_dir.mkdir()
        (job_dir / "state.json").write_text(
            json.dumps({"pid": self.dead_pid(), "pgid": 1,
                        "session": {"id": "x"}, "attempt": 1}) + "\n",
            encoding="utf-8",
        )
        self.assertTrue((job_dir / "state.json").is_file())

        before_head = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("checkout", "-b", "moved")
        code, out, err = self.run_main(["close", launched_id])
        text = self.assert_refusal(
            code, out, err, ident="record-target",
            phrases=["record-target", "work", "moved"],
        )
        self.assertIn("work", text)
        self.assertIn("moved", text)
        self.assertTrue(dest.is_file(), "the record file is left in place")
        self.assertEqual(
            self._git("rev-parse", "HEAD").stdout.strip(),
            self._git("rev-parse", "moved").stdout.strip(),
        )
        # Nothing committed on `moved`.
        log = self._git("log", "-1", "--format=%s").stdout
        self.assertNotIn(launched_id, log)
        self.assertEqual(
            self._git("rev-parse", "work").stdout.strip(), before_head,
        )


class StaleBaseIsTheOneOverridableRefusal(ls._TempLaunch):
    """D5 / contract stale-base / plan (d)(e)."""

    def test_a_ref_that_is_not_an_ancestor_of_lineage_is_refused(self):
        code, out, err = self.dispatch(
            self.argv_for(ref=self.side_sha, lineage="work", stage="plan"),
        )
        self.assert_refusal(
            code, out, err, ident="stale-base",
            phrases=["stale-base", self.side_sha[:8], "work"],
        )
        self.assert_not_started()

    def test_a_ref_far_behind_the_tip_but_on_the_branch_is_not_refused(self):
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan", ref=self.root_sha,
        )
        self.assertEqual(rec["snapshot"]["ref_sha"], self.root_sha)
        behind = rec["snapshot"]["behind_tip"]
        self.assertIsInstance(behind, int)
        self.assertGreater(behind, 0, "root is behind the tip; this is a fact")
        self.assertEqual(rec.get("overrides") or [], [])

    def test_a_ref_at_the_tip_is_not_stale_and_behind_tip_is_zero(self):
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan", ref=self.ref,
        )
        self.assertEqual(rec["snapshot"]["behind_tip"], 0)
        self.assertEqual(rec.get("overrides") or [], [])

    def test_override_stale_base_with_a_reason_launches_and_is_recorded(self):
        rec, *_ = self.launch_ok(self.argv_for(
            ref=self.side_sha, lineage="work", stage="plan", extra=[
                "--override", "stale-base:owner said continue",
            ],
        ))
        overrides = rec.get("overrides")
        self.assertIsInstance(overrides, list)
        self.assertEqual(len(overrides), 1, "exactly one override landed")
        item = overrides[0]
        self.assertEqual(item.get("refusal"), "stale-base")
        self.assertEqual(item.get("reason"), "owner said continue")
        self.assertEqual(item.get("by"), ls.AGENT)
        msg = self._git("log", "-1", "--format=%B").stdout
        self.assertIn("stale-base", msg)
        self.assertIn("owner said continue", msg)

    def test_an_override_on_identity_is_itself_refused(self):
        os.environ.pop("WF_AGENT", None)
        before = list(self.jobs_root.iterdir())
        code, out, err = self.dispatch(self.argv_for(
            stage="plan", extra=["--override", "identity:please"],
        ))
        text = self.combined(out, err).lower()
        self.assertEqual(code, ls.REFUSAL_EXIT)
        self.assertTrue(
            "override" in text or "identity" in text,
            f"non-overridable override must be refused: {text!r}",
        )
        self.assertEqual(list(self.jobs_root.iterdir()), before)
        self.assert_not_started()

    def test_an_empty_reason_is_refused(self):
        code, out, err = self.dispatch(self.argv_for(
            ref=self.side_sha, stage="plan", extra=["--override", "stale-base:"],
        ))
        self.assertEqual(code, ls.REFUSAL_EXIT)
        text = self.combined(out, err).lower()
        self.assertTrue(
            "reason" in text or "override" in text,
            f"empty reason must be refused: {text!r}",
        )
        self.assert_not_started()


class ModelIsRequiredAndCodexEffortIsRefused(ls._TempLaunch):
    """D6 / contract model / plan (l)."""

    def test_no_model_is_refused_and_nothing_launches(self):
        argv = self.argv_for(stage="plan", model=None)
        self.assertNotIn("--model", argv)
        code, out, err = self.dispatch(argv)
        self.assert_refusal(
            code, out, err, ident="model",
            phrases=["model", "--model"],
        )
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])

    def test_effort_on_codex_is_refused(self):
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan", extra=["--effort", "high"],
        ))
        self.assert_refusal(
            code, out, err, ident="model",
            phrases=["effort", "codex"],
        )
        self.assert_not_started()

    def test_effort_on_grok_is_the_silent_neighbour(self):
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="plan", harness="grok", stage="plan", extra=["--effort", "high"],
        ))
        argv = [str(p) for p in witness["argv"]]
        self.assertIn("--reasoning-effort", argv)
        self.assertEqual(argv[argv.index("--reasoning-effort") + 1], "high")
        self.assertEqual(rec["model"]["effort_requested"], "high")


class ClaudeWriteIsUnadmitted(ls._TempLaunch):
    """D7 / contract write-role-unadmitted / plan (l)."""

    def test_a_claude_write_role_is_refused(self):
        code, out, err = self.dispatch(self.argv_for(
            job="implement", harness="claude", stage="code",
            scope="python3 -m unittest",
        ))
        self.assert_refusal(
            code, out, err, ident="write-role-unadmitted",
            phrases=["write-role-unadmitted", "claude"],
        )
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])

    def test_claude_author_tests_is_refused_the_same_way(self):
        # The unadmitted rule is the write role, not the implement job
        # name. author-tests is the other write job in the registry.
        code, out, err = self.dispatch(self.argv_for(
            job="author-tests", harness="claude", stage="tests",
            scope="pin the contract",
        ))
        self.assert_refusal(
            code, out, err, ident="write-role-unadmitted",
            phrases=["write-role-unadmitted", "claude"],
        )
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])

    def test_a_claude_read_role_is_the_silent_neighbour(self):
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="plan", harness="claude", stage="plan",
        ))
        self.assertEqual(rec["role"], "read")
        self.assertEqual(rec["harness"]["name"], "claude")
        self.assertTrue(witness["argv"])

    def test_a_grok_write_role_is_admitted(self):
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        self.assertEqual(rec["role"], "write")


class ScopeCapIs1024AndAdjudicateTakesNone(ls._TempLaunch):
    """D8 / contract scope-cap / plan (m)(n)."""

    def test_a_scope_of_1025_bytes_is_refused(self):
        scope = "x" * (ls.SCOPE_CAP + 1)
        self.assertEqual(len(scope.encode("utf-8")), 1025)
        code, out, err = self.dispatch(self.argv_for(
            stage="plan", scope=scope,
        ))
        self.assert_refusal(
            code, out, err, ident="scope-cap",
            phrases=["scope-cap", "1024"],
        )
        self.assert_not_started()

    def test_a_scope_of_1024_bytes_is_the_silent_neighbour(self):
        scope = "y" * ls.SCOPE_CAP
        self.assertEqual(len(scope.encode("utf-8")), 1024)
        rec, *_ = self.launch_ok(self.argv_for(stage="plan", scope=scope))
        self.assertEqual(rec["brief"]["scope"], scope)
        self.assertEqual(len(rec["brief"]["scope"].encode("utf-8")), 1024)

    def test_adjudicate_refuses_a_scope(self):
        report = self.home / "in" / "report.md"
        self.plant_new_file(report, "# report\n", must_contain="report")
        code, out, err = self.dispatch(self.argv_for(
            job="adjudicate", harness="grok", stage="adjudicate",
            scope="this job takes none", extra=["--input", str(report)],
        ))
        self.assert_refusal(
            code, out, err, ident="scope-cap",
            phrases=["scope-cap", "adjudicate"],
        )
        self.assert_not_started()

    def test_adjudicate_without_scope_is_the_silent_neighbour(self):
        report = self.home / "in" / "report.md"
        self.plant_new_file(report, "# report\n", must_contain="report")
        rec, *_ = self.launch_ok(self.argv_for(
            job="adjudicate", harness="grok", stage="adjudicate",
            scope=None, extra=["--input", str(report)],
        ))
        self.assertIn(rec["brief"].get("scope"), (None, "", []))
        self.assertEqual(rec["job"], "adjudicate")


class HistoryVersusWithheldAndModeUnavailable(ls._TempLaunch):
    """D9 / contract history-vs-withheld, mode-unavailable."""

    def test_withheld_and_whole_together_are_refused(self):
        code, out, err = self.dispatch(self.argv_for(
            job="withheld-whole", harness="grok", stage="plan",
        ))
        self.assert_refusal(
            code, out, err, ident="history-vs-withheld",
            phrases=["history-vs-withheld", "withheld-whole"],
        )
        self.assert_not_started()

    def test_a_fileset_job_is_refused_by_name_until_that_mode_lands(self):
        code, out, err = self.dispatch(self.argv_for(
            job="fileset-job", harness="grok", stage="plan",
        ))
        self.assert_refusal(
            code, out, err, ident="mode-unavailable",
            phrases=["mode-unavailable", "fileset"],
        )
        self.assert_not_started()


class IsolationRefusesUnknownAndDirtyHomes(ls._TempLaunch):
    """D10 / contract isolation."""

    def test_an_unknown_harness_is_refused(self):
        code, out, err = self.dispatch(self.argv_for(
            harness="not-a-harness", stage="plan",
        ))
        self.assertEqual(code, ls.REFUSAL_EXIT)
        text = self.combined(out, err)
        self.assertTrue(
            "isolation" in text.lower() or "not-a-harness" in text,
            f"unknown harness must be refused by isolation: {text!r}",
        )
        self.assert_not_started()

    def test_a_missing_codex_credential_is_refused(self):
        auth = self.home / ".codex" / "auth.json"
        self.assertTrue(auth.is_file(), "plant: credential present before unlink")
        auth.unlink()
        self.assertFalse(auth.exists(), "plant: credential is gone")
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan",
        ))
        self.assertEqual(code, ls.REFUSAL_EXIT)
        text = self.combined(out, err).lower()
        self.assertTrue(
            "isolation" in text or "auth.json" in text or "credential" in text,
            f"missing credential must surface isolation.NotIsolated: {text!r}",
        )
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])

    def test_a_missing_grok_credential_is_refused(self):
        auth = self.home / ".grok" / "auth.json"
        self.assertTrue(auth.is_file(), "plant: credential present before unlink")
        auth.unlink()
        self.assertFalse(auth.exists(), "plant: credential is gone")
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
        ))
        self.assertEqual(code, ls.REFUSAL_EXIT)
        text = self.combined(out, err).lower()
        self.assertTrue(
            "isolation" in text or "auth.json" in text or "credential" in text,
            f"missing credential must surface isolation.NotIsolated: {text!r}",
        )
        self.assert_not_started()

    def test_a_dirty_minimal_home_is_refused(self):
        job_id = "20260826T000000Z-plan-codex-d1r7ee"
        self.force_id(job_id)
        dirty = self.jobs_root / job_id / "home" / "codex"
        dirty.mkdir(parents=True)
        leftover = dirty / "hooks.json"
        leftover.write_text("{}\n", encoding="utf-8")
        self.assertEqual(
            leftover.read_text(encoding="utf-8"), "{}\n",
            "plant: leftover landed in the would-be minimal home",
        )
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan",
        ))
        self.assertEqual(code, ls.REFUSAL_EXIT)
        text = self.combined(out, err).lower()
        self.assertTrue(
            "isolation" in text or "not empty" in text or "hooks.json" in text,
            f"dirty minimal home must be NotIsolated: {text!r}",
        )
        self.assert_not_started()

    def test_an_isolated_grok_home_holds_exactly_auth_json(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        home = witness["env"].get("GROK_HOME")
        self.assertTrue(home, "isolated grok launch sets GROK_HOME")
        from pathlib import Path
        names = sorted(p.name for p in Path(home).iterdir())
        self.assertEqual(names, ["auth.json"])
        self.assertEqual(rec["harness"]["name"], "grok")


class MissingLineageBranchIsRefused(ls._TempLaunch):
    """No dedicated refusal id: contract record-target already covers
    current branch ≠ lineage. The text must name record-target and
    expected/found/satisfy, not merely exit 3 and the branch string."""

    def test_a_lineage_branch_that_does_not_exist_locally_is_refused(self):
        code, out, err = self.dispatch(self.argv_for(
            lineage="no-such-lineage", stage="plan",
        ))
        self.assert_refusal(
            code, out, err, ident="record-target",
            phrases=["record-target", "no-such-lineage", "work"],
        )
        self.assert_not_started()
        self.assertEqual(self.job_dirs(), [])
