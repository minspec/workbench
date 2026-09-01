"""Red pins for dispatch/review-fixes p1 findings G1 and G2.

Authored from CONTRACT.md §Dispatch (Verbs, Template values, Isolation
per harness, The record permitted delta) plus the two p1 findings at
``.dev/records/dispatches/20260828T224426Z-review-claude-043571.json``.

  G1  claude --add-dir names directories, so the child can open {diff}
  G2  a later launch does not move a closed, resumed job's record or
      job directory off the canonical paths
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import launch_support as ls


def _add_dir_values(argv, cwd):
    """Every --add-dir value, resolved the way the child would see it."""
    found = []
    argv = [str(a) for a in argv]
    i = 0
    while i < len(argv):
        a = argv[i]
        raw = None
        if a == "--add-dir" and i + 1 < len(argv):
            raw = argv[i + 1]
            i += 2
        elif a.startswith("--add-dir="):
            raw = a.split("=", 1)[1]
            i += 1
        else:
            i += 1
            continue
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = Path(cwd) / p
        found.append(p.resolve())
    return found


def _inside_directory(path, root):
    """True when ``path`` lives inside directory ``root``.

    ``--add-dir`` names a directory. Granting the file itself
    (``path == root`` when ``root`` is not a directory) is not a grant.
    """
    if not root.is_dir():
        return False
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class ClaudeAddDirValuesAreDirectories(ls._TempLaunch):
    """G1 / CONTRACT.md §Template values {diff} and Isolation.

    --add-dir names an additional working DIRECTORY. Passing the
    {diff} FILE as an --add-dir root does not let a claude child open
    it; observed live on 20260828T224426Z-review-claude-043571.
    """

    def setUp(self):
        super().setUp()
        jobs = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        jobs["names-diff"] = {
            "adapter": "harness",
            "deliverable": "fixture: {diff} named on its own line",
            "role": "read",
            "snapshot": "whole",
            "prompt": "diff={diff}\nAim at: {scope}",
            "constraints": ["read only"],
        }
        self.jobs_file.write_text(
            json.dumps(jobs, indent=2) + "\n", encoding="utf-8",
        )
        self.ref = self._commit("fixture job that names diff")

    def _prompt_field(self, prompt, key):
        prefix = key + "="
        for line in prompt.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
        self.fail(f"rendered brief has no {key}= line:\n{prompt}")

    def test_claude_add_dir_values_are_directories_so_diff_is_openable(self):
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="names-diff", harness="claude", stage="review",
            scope="pin --add-dir as directories for {diff}",
        ))
        argv = [str(a) for a in witness["argv"]]
        self.assertTrue(argv, "child argv is empty")
        self.assertEqual(
            Path(argv[0]).name, "claude",
            f"this pin is for a claude child, argv[0]={argv[0]!r}",
        )
        cwd = witness.get("cwd")
        self.assertTrue(cwd, "child cwd is recorded")
        prompt = witness.get("stdin") or ""
        self.assertTrue(prompt, "claude receives the rendered brief on stdin")
        named = self._prompt_field(prompt, "diff").strip()
        self.assertTrue(
            named,
            "{diff} must name the diff the launcher writes, not a hole "
            f"in the brief; got {named!r} in:\n{prompt}",
        )
        self.assertNotIn(" ", named, f"{{diff}} is one path, got {named!r}")
        diff_path = Path(named)
        if not diff_path.is_absolute():
            diff_path = Path(cwd) / diff_path
        diff_path = diff_path.resolve()
        self.assertTrue(
            diff_path.is_file(),
            f"{{diff}} must name a file the child can open, got {named!r}",
        )
        self.assertEqual(rec["job"], "names-diff")

        add_dirs = _add_dir_values(argv, cwd)
        self.assertTrue(
            add_dirs,
            "claude argv must include --add-dir so {diff} outside "
            f"snapshot/ is granted; argv={argv!r}",
        )
        not_dirs = [str(p) for p in add_dirs if not p.is_dir()]
        self.assertEqual(
            not_dirs, [],
            "--add-dir names a directory, not a file; the claude child "
            "cannot open a path that was itself passed as --add-dir. "
            f"non-directories={not_dirs!r} argv={argv!r} {{diff}}={named!r}",
        )
        self.assertTrue(
            any(_inside_directory(diff_path, root) for root in add_dirs),
            f"claude child cannot read {{diff}} path {diff_path}: "
            "outside this session's allowed working directories "
            f"(cwd={cwd!r}, add-dir={[str(r) for r in add_dirs]})",
        )


class ClosedResumedJobStaysAtCanonicalPaths(ls._TempLaunch):
    """G2 / CONTRACT.md §The record permitted delta.

    One file per dispatch at .dev/records/dispatches/<id>.json. The
    permitted delta is that file plus its commit; the index and
    worktree are otherwise untouched. A later launch must not
    shutil.move a closed, resumed job's record or job directory.
    """

    def test_a_later_launch_does_not_archive_a_closed_resumed_job(self):
        rec_a, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        id_a = rec_a["id"]
        record_rel = ".dev/records/dispatches/" + id_a + ".json"
        path_a = self.repo / record_rel
        job_a = self.jobs_root / id_a
        self.assertTrue(
            path_a.is_file(),
            f"plant: first record is at the canonical path {path_a}",
        )
        self.assertTrue(
            job_a.is_dir(),
            f"plant: first job directory is under jobs-root {job_a}",
        )
        tracked = self._git("ls-files", "--", record_rel).stdout.strip()
        self.assertEqual(
            tracked, record_rel,
            "plant: first record is a tracked file on the lineage branch",
        )
        self.assertEqual(rec_a["status"], "closed")

        self.witness.unlink()
        code, out, err = self.run_main(["resume", id_a])
        self.assertEqual(
            code, 0,
            f"resume of the closed job must succeed: {self.combined(out, err)}",
        )
        self.assertTrue(
            path_a.is_file(),
            "resume itself must not move the record off the canonical path",
        )
        self.assertTrue(job_a.is_dir(), "resume keeps the same job directory")

        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan",
        ))
        self.assertEqual(
            code, 0,
            f"second launch must succeed: {self.combined(out, err)}",
        )

        self.assertTrue(
            path_a.is_file(),
            "CONTRACT.md §The record: one file per dispatch at "
            f"{record_rel}; a later launch must not move a closed, "
            "resumed job's tracked record to "
            ".dev/records/dispatches/archive/",
        )
        still_tracked = self._git("ls-files", "--", record_rel).stdout.strip()
        self.assertEqual(
            still_tracked, record_rel,
            "the first record stays tracked; git commit --only of the "
            "new record must not be covering a working-tree deletion",
        )
        porcelain = self.porcelain()
        deleted = [
            line for line in porcelain.splitlines()
            if re.search(r"^D\s+", line)
            and record_rel in line
        ]
        self.assertEqual(
            deleted, [],
            "CONTRACT.md §The record permitted delta: the index and "
            "worktree are otherwise untouched; a later launch must not "
            f"delete the tracked record {record_rel}: {porcelain!r}",
        )
        archive_record = (
            self.repo / ".dev" / "records" / "dispatches" / "archive"
            / f"{id_a}.json"
        )
        self.assertFalse(
            archive_record.exists(),
            f"record must not be moved to {archive_record}",
        )
        self.assertTrue(
            job_a.is_dir(),
            f"job directory must stay at {job_a}, not be moved to "
            f"{self.jobs_root.name}-archive/",
        )
        jobs_archive = self.jobs_root.parent / (self.jobs_root.name + "-archive")
        self.assertFalse(
            (jobs_archive / id_a).exists(),
            f"job directory must not be moved to {jobs_archive / id_a}",
        )

        try:
            code, out, err = self.run_main(["brief", "--check", id_a])
        except Exception as exc:
            self.fail(
                f"brief --check {id_a} must find the record at "
                f"{record_rel}; raised {type(exc).__name__}: {exc}"
            )
        self.assertEqual(
            code, 0,
            f"brief --check {id_a} must find the record at {record_rel}: "
            f"{self.combined(out, err)}",
        )
        try:
            code, out, err = self.run_main(["status", id_a])
        except Exception as exc:
            self.fail(
                f"status {id_a} must find the job; raised "
                f"{type(exc).__name__}: {exc}"
            )
        text = self.combined(out, err)
        self.assertNotIn(
            "unlaunched", text.lower(),
            f"status {id_a} must not report unlaunched after a later "
            f"launch: {text!r}",
        )
        self.assertEqual(
            code, 0,
            f"status {id_a} exits 0: {text!r}",
        )
