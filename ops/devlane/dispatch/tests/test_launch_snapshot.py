"""Whole-mode snapshot, permitted delta, FILESET.md wall.

Written from CONTRACT.md §Dispatch Snapshot modes, Job directory,
Collect, The record (permitted delta). Plan items (a)(b)(c)(m).

  S1  invoking repo delta is exactly the permitted set
  S2  --only: an unrelated staged file stays staged
  S3  FETCH_HEAD unchanged
  S4  whole mint guarantees (HEAD, parents, remote, alternates, logs,
      source path, clean status, not a worktree)
  S5  FILESET.md plant fires the wall; the launcher snapshot stays clean
  S6  prompt and bookkeeping live outside snapshot/
"""

from __future__ import annotations

import hashlib
import os
import subprocess

import launch_support as ls


class PermittedDeltaToTheInvokingRepository(ls._TempLaunch):
    """S1 / contract permitted delta / plan (a)."""

    def test_a_read_dispatch_adds_one_record_commit_and_no_dispatch_ref(self):
        before_head = self._git("rev-parse", "HEAD").stdout.strip()
        before_tree = self._git("rev-parse", "HEAD^{tree}").stdout.strip()
        before_refs = self.refs_map()
        before_fetch = self.fetch_head_bytes()
        before_status = self.porcelain()
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        after_head = self._git("rev-parse", "HEAD").stdout.strip()
        after_refs = self.refs_map()
        self.assertEqual(
            self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            "work",
        )
        self.assertNotEqual(after_head, before_head, "one new commit")
        self.assertEqual(
            self._git("rev-parse", "HEAD^").stdout.strip(), before_head,
        )
        names = self._git(
            "diff-tree", "--no-commit-id", "--name-only", "-r",
            before_head, after_head,
        ).stdout.splitlines()
        record_rel = (
            ".dev/records/dispatches/" + rec["id"] + ".json"
        )
        self.assertEqual(names, [record_rel])
        self.assertNotIn(
            f"refs/dispatch/{rec['id']}", after_refs,
            "read roles do not fetch refs/dispatch",
        )
        extra = set(after_refs) - set(before_refs)
        self.assertEqual(extra, set())
        for name in before_refs:
            if name == "refs/heads/work":
                continue
            self.assertEqual(after_refs[name], before_refs[name], name)
        self.assertEqual(self.fetch_head_bytes(), before_fetch)
        # The record is committed, so it leaves the index; nothing else
        # about the index or worktree moves.
        self.assertEqual(self.porcelain(), before_status)
        self.assertNotEqual(
            self._git("rev-parse", "HEAD^{tree}").stdout.strip(),
            before_tree,
        )

    def test_a_write_dispatch_adds_the_record_commit_and_one_dispatch_ref(self):
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        before_head = self._git("rev-parse", "HEAD").stdout.strip()
        before_refs = self.refs_map()
        before_fetch = self.fetch_head_bytes()
        before_status = self.porcelain()
        before_index = self.index_blob()
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        after_head = self._git("rev-parse", "HEAD").stdout.strip()
        after_refs = self.refs_map()
        self.assertEqual(
            self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            rec["lineage"]["branch"],
        )
        names = self._git(
            "diff-tree", "--no-commit-id", "--name-only", "-r",
            before_head, after_head,
        ).stdout.splitlines()
        record_rel = ".dev/records/dispatches/" + rec["id"] + ".json"
        self.assertEqual(names, [record_rel])
        dispatch_ref = f"refs/dispatch/{rec['id']}"
        self.assertIn(dispatch_ref, after_refs)
        extra = set(after_refs) - set(before_refs)
        self.assertEqual(extra, {dispatch_ref})
        for name in before_refs:
            if name == "refs/heads/work":
                continue
            self.assertEqual(after_refs[name], before_refs[name], name)
        self.assertEqual(self.fetch_head_bytes(), before_fetch)
        snap_head = rec["result"]["head"]
        self.assertEqual(after_refs[dispatch_ref], snap_head)
        self.assertNotEqual(snap_head, rec["snapshot"]["ref_sha"])
        # Index and worktree otherwise untouched — not just the commit tree.
        self.assertEqual(self.porcelain(), before_status)
        after_index = self.index_blob()
        record_entry = self._git("ls-files", "-s", "--", record_rel).stdout
        self.assertTrue(record_entry.strip())
        self.assertEqual(after_index.replace(record_entry, ""), before_index)
        reflog = self.dispatch_reflog(rec["id"])
        self.assertTrue(
            reflog.strip(),
            "write collect writes a reflog line for refs/dispatch/<id>",
        )
        self.assertIn(snap_head[:8], reflog)


class CommitOnlyLeavesUnrelatedIndexEntries(ls._TempLaunch):
    """S2 / contract git commit --only."""

    def test_a_planted_unrelated_staged_file_stays_staged_and_uncommitted(self):
        planted = self._write("unrelated.txt", "do not sweep me in\n")
        self._git("add", "--", "unrelated.txt")
        staged_before = self._git("diff", "--cached", "--name-only").stdout
        self.assertIn("unrelated.txt", staged_before, "plant: file is staged")
        self.assertTrue(planted.is_file())
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        staged_after = self._git("diff", "--cached", "--name-only").stdout
        self.assertIn("unrelated.txt", staged_after)
        committed = self._git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD",
        ).stdout.splitlines()
        self.assertNotIn("unrelated.txt", committed)
        self.assertEqual(
            committed,
            [".dev/records/dispatches/" + rec["id"] + ".json"],
        )
        msg = self._git("log", "-1", "--format=%B").stdout
        self.assertIn(f"dispatch: record {rec['id']}", msg)
        self.assertIn("Source: generated: ops/devlane/dispatch/launch.py", msg)
        self.assertIn(f"Co-Authored-By: {ls.AGENT}", msg)


class FetchHeadIsNotWritten(ls._TempLaunch):
    """S3 — collect uses --no-write-fetch-head; mint removes FETCH_HEAD."""

    def test_an_existing_fetch_head_is_byte_identical_after_a_write_dispatch(self):
        fetch = self.repo / ".git" / "FETCH_HEAD"
        marker = b"planted-fetch-head-marker\n"
        fetch.write_bytes(marker)
        self.assertEqual(fetch.read_bytes(), marker, "plant landed")
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="grok", stage="code",
            scope="python3 -m unittest",
        ))
        self.assertEqual(fetch.read_bytes(), marker)
        self.assertIn(f"refs/dispatch/{rec['id']}", self.refs_map())

    def test_absent_fetch_head_stays_absent_after_a_read_dispatch(self):
        fetch = self.repo / ".git" / "FETCH_HEAD"
        self.assertFalse(fetch.exists())
        self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertFalse(fetch.exists())


class WholeMintGuarantees(ls._TempLaunch):
    """S4 / contract whole / plan (b)."""

    def test_head_equals_ref_sha_and_parents_are_present(self):
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan", ref=self.mid_sha,
        )
        snap = self.snapshot_of(rec)
        head = self._git("rev-parse", "HEAD", repo=snap).stdout.strip()
        self.assertEqual(head, self.mid_sha)
        self.assertEqual(head, rec["snapshot"]["ref_sha"])
        parent = self._git("rev-parse", "HEAD^", repo=snap).stdout.strip()
        self.assertEqual(parent, self.root_sha)
        kind = self._git("cat-file", "-t", self.root_sha, repo=snap)
        self.assertEqual(kind.stdout.strip(), "commit")

    def test_remote_is_empty_and_alternates_and_logs_do_not_exist(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = self.snapshot_of(rec)
        remote = self._git("remote", repo=snap).stdout.strip()
        self.assertEqual(remote, "")
        git = snap / ".git"
        self.assertTrue(git.exists())
        self.assertFalse(
            (git / "objects" / "info" / "alternates").exists(),
            "fetch-minted snapshots do not share objects via alternates",
        )
        self.assertFalse(
            (git / "logs").exists(),
            "core.logAllRefUpdates=false and no logs/ directory",
        )

    def test_object_store_is_not_shared_by_symlink_or_hardlink(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assert_objects_not_shared(self.snapshot_of(rec))

    def test_the_invoking_repo_path_occurs_in_no_byte_under_dot_git(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = self.snapshot_of(rec)
        needle = str(self.repo.resolve()).encode("utf-8")
        self.assertTrue(needle, "source path must be a real needle")
        hits = self.git_dir_mentions(snap, needle)
        self.assertEqual(
            hits, [],
            f"source path leaked into snapshot .git: {hits}",
        )

    def test_snapshot_status_is_empty_before_the_harness_runs(self):
        rec, witness, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = self.snapshot_of(rec)
        # After a read role the tree should still be clean: the worker
        # is contracted not to edit, and the fake does not.
        self.assertEqual(self.porcelain(repo=snap), "")
        self.assertEqual(
            os.path.realpath(witness["cwd"]),
            os.path.realpath(snap),
        )

    def test_uncommitted_invoking_bytes_are_not_in_the_snapshot(self):
        dirty = self._write("alpha.py", "DIRTY WORKTREE\n")
        self.assertIn("DIRTY WORKTREE", dirty.read_text(encoding="utf-8"))
        rec, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan", ref=self.ref,
        )
        snap = self.snapshot_of(rec)
        snap_alpha = (snap / "alpha.py").read_text(encoding="utf-8")
        self.assertEqual(snap_alpha, "alpha v3\n")
        self.assertNotIn("DIRTY WORKTREE", snap_alpha)
        self.assertEqual(
            self._git("rev-parse", "HEAD", repo=snap).stdout.strip(),
            self.ref,
        )

    def test_the_snapshot_is_not_in_the_invoking_repo_worktree_list(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = os.path.realpath(self.snapshot_of(rec))
        listed = [os.path.realpath(p) for p in self.worktree_paths()]
        self.assertNotIn(snap, listed)
        self.assertTrue(
            (self.snapshot_of(rec) / ".git").exists(),
            "whole mode has its own .git — this is not fileset",
        )


class FilesetManifestMustNotLandInTheSnapshot(ls._TempLaunch):
    """S5 / plan (c) — a planted FILESET.md listing receipts.py fires
    the wall; the launcher's snapshot stays clean of that file."""

    def test_a_planted_fileset_md_fires_the_wall_and_the_snapshot_does_not(self):
        self.assertTrue(ls.WALL_PY.is_file(), "vocabulary_wall.py must exist")
        plant_root = self.home / "wall-plant"
        plant_root.mkdir()
        fileset = plant_root / "FILESET.md"
        self.plant_new_file(
            fileset,
            "included:\n  ops/devlane/workflow/receipts.py\n",
            must_contain="receipts.py",
        )
        receipts = plant_root / ".dev" / "app" / "workflow" / "receipts.py"
        # The file is under .dev/ so the wall's default walk would skip
        # it; FILESET.md at the root is the design-surface hit.
        self.plant_new_file(receipts, "# receipts fixture\n",
                            must_contain="receipts")
        wall = subprocess.run(
            ["python3", str(ls.WALL_PY), "--root", str(plant_root)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            wall.returncode, 1,
            "plant must make the wall fire, got "
            f"{wall.returncode}: {wall.stdout}{wall.stderr}",
        )
        self.assertIn("receipts", wall.stdout.lower())

        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = self.snapshot_of(rec)
        self.assertFalse(
            (snap / "FILESET.md").exists(),
            "launcher snapshot must not carry FILESET.md at the root",
        )
        self.assertFalse((snap / "FILESET.diff").exists())
        clean = subprocess.run(
            ["python3", str(ls.WALL_PY), "--root", str(snap)],
            capture_output=True, text=True,
        )
        self.assertNotIn("FILESET.md", clean.stdout)
        self.assertNotEqual(
            clean.returncode, 1,
            f"launcher snapshot must not trip the wall: {clean.stdout}",
        )


class PromptAndBookkeepingLiveInTheJobDirectory(ls._TempLaunch):
    """S6 / contract job directory / plan (m)."""

    def test_prompt_path_is_outside_the_snapshot_and_matches_the_cli_bytes(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
            scope="digest-me-exactly",
        )
        snap = self.snapshot_of(rec).resolve()
        job_dir = self.the_job_dir().resolve()
        prompt = job_dir / "prompt.txt"
        self.assertTrue(prompt.is_file(), "prompt.txt lives in the job dir")
        self.assertFalse(
            (snap / "prompt.txt").exists(),
            "prompt must not be written inside the snapshot",
        )
        self.assertFalse(snap in prompt.parents or prompt.parent == snap)
        body = prompt.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        self.assertEqual(rec["brief"]["sha256"], digest)
        self.assertEqual(rec["brief"]["bytes"], len(body))
        received = witness.get("prompt_text", "")
        self.assertTrue(received, "the fake CLI must have read the prompt file")
        self.assertEqual(
            hashlib.sha256(received.encode("utf-8")).hexdigest(), digest,
        )
        self.assertIn("digest-me-exactly", received)
        self.assertEqual(
            os.path.realpath(witness.get("prompt_file") or ""),
            os.path.realpath(prompt),
        )

    def test_launcher_bookkeeping_is_not_under_snapshot(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        snap = self.snapshot_of(rec)
        job_dir = self.the_job_dir()
        for name in (
            "prompt.txt", "raw.out", "stderr", "breaker.log",
            "TRIPPED.md", "state.json", "exit",
        ):
            self.assertFalse(
                (snap / name).exists(),
                f"{name} must not land under snapshot/",
            )
        self.assertTrue(
            (job_dir / "state.json").is_file()
            or (job_dir / "exit").is_file(),
            "job dir carries state.json and/or exit",
        )
        for required in ("snapshot", "prompt.txt", "in", "out"):
            self.assertTrue(
                (job_dir / required).exists(),
                f"job dir layout includes {required}",
            )
