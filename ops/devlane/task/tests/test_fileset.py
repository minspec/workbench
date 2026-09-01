"""The fileset: the smallest snapshot a task needs, and the proof
that the pruning did not change the answer.

Written from SPEC.md, before the module existed. Each test names the
contract rule it pins:

  B1  derive is the changed paths, sorted and unique
  B2  derive also takes repo-relative paths named in the diff text
  B3  a path that does not exist at ref is never in the derived set
  B4  derive is deterministic
  B5  snapshot(include=...) writes exactly that set
  B6  a missing include is a FilesetError, not a skip
  B7  an empty include set is a FilesetError
  B8  snapshot(whole=True) writes every file tracked at ref
  B9  FILESET.diff is written iff a base is given
  B10 FILESET.md names the ref, the base, and every included path
  B11 snapshot never modifies the source repo
  B12 manifest bytes equal the summed size of the files written
  B13 parity is True iff whole-tree and pruned results match
  B14 a command that cannot run raises; identical failure is not that
  B15 main is a CLI: JSON on 0, FilesetError on 1, usage on 64
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

fileset = support.load("fileset")

_META = frozenset({"FILESET.md", "FILESET.diff"})


def _git_env(home: Path) -> dict:
    # GIT_DIR / GIT_WORK_TREE in the caller environment would aim
    # git at the snapshot (or its parent). The fixtures must be
    # closed worlds.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME"}
    env.update({
        "HOME": str(home),
        "GIT_AUTHOR_NAME": "fileset-test",
        "GIT_AUTHOR_EMAIL": "fileset-test@example.test",
        "GIT_COMMITTER_NAME": "fileset-test",
        "GIT_COMMITTER_EMAIL": "fileset-test@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def payload_paths(into) -> list[str]:
    root = Path(into)
    if not root.exists():
        return []
    found = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if rel not in _META:
                found.append(rel)
    return sorted(found)


def all_file_paths(into) -> list[str]:
    root = Path(into)
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*") if p.is_file())


@contextlib.contextmanager
def _cwd(path):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def run_main(argv, *, cwd):
    out, err = io.StringIO(), io.StringIO()
    with _cwd(cwd), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        code = fileset.main(argv)
    return code, out.getvalue(), err.getvalue()


class _TempRepo(unittest.TestCase):
    """A throwaway git repo. Never the snapshot's own tree."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.repo = self.home / "repo"
        self.into = self.home / "into"
        self.pruned = self.home / "pruned"
        self.repo.mkdir()
        self.into.mkdir()
        self.env = _git_env(self.home)
        # Branch name is irrelevant: every test pins SHAs, not HEAD.
        self._git("init")
        self._git("config", "user.name", "fileset-test")
        self._git("config", "user.email", "fileset-test@example.test")
        self._git("config", "commit.gpgsign", "false")

    def tearDown(self):
        self._td.cleanup()

    def _git(self, *args):
        r = subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env,
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"git {args} failed ({r.returncode}): {r.stderr}")
        return r

    def _write(self, rel, content):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("commit", "-m", msg)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _fingerprint(self):
        """User-visible repo state. Not .git bytes — status updates
        those without having checked anything out."""
        files = {}
        for p in self.repo.rglob("*"):
            if ".git" in p.parts or not p.is_file():
                continue
            rel = p.relative_to(self.repo).as_posix()
            files[rel] = (
                p.stat().st_mode,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        return {
            "files": files,
            "head": self._git("rev-parse", "HEAD").stdout,
            "status": self._git("status", "--porcelain=v1", "-uall").stdout,
            "index": self._git("ls-files", "-s").stdout,
            "diff": self._git("diff").stdout,
            "staged": self._git("diff", "--cached").stdout,
        }


def _standard_history(repo_test: _TempRepo):
    """Two commits. The ref diff deletes one file, changes one,
    adds a test that names two unchanged paths, and leaves a
    bystander unmentioned."""
    repo_test._write("alpha.py", "alpha v1\n")
    repo_test._write("deleted_by_the_diff.py", "goodbye\n")
    repo_test._write("config/app.toml", "name = 'app'\n")
    repo_test._write("tools/doit.py", "print('doit')\n")
    repo_test._write("pkg/mod.py", "x = 1\n")
    repo_test._write("beta.py", "unchanged\n")
    base = repo_test._commit("base")

    repo_test._write("alpha.py", "alpha v2\n")
    (repo_test.repo / "deleted_by_the_diff.py").unlink()
    repo_test._write("zeta.py", "new at ref\n")
    # Paths sit on their own lines so a scan of the diff text can
    # see them without having to parse Python.
    repo_test._write(
        "tests/test_alpha.py",
        "tools/doit.py\n"
        "config/app.toml\n")
    ref = repo_test._commit("ref")
    return base, ref


# The set B1+B2 require of `_standard_history`. Sorted, unique,
# and without the deleted path (B3).
DERIVED = [
    "alpha.py",
    "config/app.toml",
    "tests/test_alpha.py",
    "tools/doit.py",
    "zeta.py",
]

TRACKED_AT_REF = [
    "alpha.py",
    "beta.py",
    "config/app.toml",
    "pkg/mod.py",
    "tests/test_alpha.py",
    "tools/doit.py",
    "zeta.py",
]


class DeriveReturnsTheChangedPaths(_TempRepo):
    """B1 — the snapshot's seed is the diff, sorted and unique."""

    def test_changed_paths_are_sorted_and_complete(self):
        self._write("zed.py", "z1\n")
        self._write("alpha.py", "a1\n")
        self._write("stay.py", "stay\n")
        base = self._commit("base")
        self._write("zed.py", "z2\n")
        self._write("alpha.py", "a2\n")
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        # git will list zed first if it walks in change order;
        # the contract is the sorted list, so two runs and two
        # implementations can be compared without a ceremony.
        self.assertEqual(got, ["alpha.py", "zed.py"])

    def test_a_path_that_is_both_changed_and_named_appears_once(self):
        self._write("src.py", "v1\n")
        base = self._commit("base")
        self._write("src.py", "v2 mentions src.py\n")
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(got, ["src.py"])
        self.assertEqual(len(got), len(set(got)))

    def test_paths_are_repo_relative_and_posix(self):
        self._write("pkg/mod.py", "v1\n")
        base = self._commit("base")
        self._write("pkg/mod.py", "v2\n")
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(got, ["pkg/mod.py"])
        for path in got:
            self.assertNotIn("\\", path)
            self.assertFalse(path.startswith("/"), path)


class DeriveAlsoTakesPathsNamedInTheDiff(_TempRepo):
    """B2 — a test naming the script it runs is how derivation
    works with no human input."""

    def test_an_unchanged_path_named_in_the_diff_is_included(self):
        self._write("script.py", "print(0)\n")
        self._write("config.toml", "n = 1\n")
        self._write("bystander.py", "leave me\n")
        self._write("test_script.py", "pass\n")
        base = self._commit("base")
        self._write(
            "test_script.py",
            "script.py\n"
            "config.toml\n")
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(got, ["config.toml", "script.py", "test_script.py"])
        self.assertNotIn("bystander.py", got)

    def test_a_named_path_that_does_not_exist_at_ref_is_not_invented(self):
        # The diff text can name anything. Only paths that are
        # real at ref belong in the snapshot; the rest is prose.
        self._write("keep.py", "k1\n")
        base = self._commit("base")
        self._write("keep.py", "k2\nno/such/path.py\n")
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(got, ["keep.py"])
        self.assertNotIn("no/such/path.py", got)


class DeriveNeverReturnsAPathMissingAtRef(_TempRepo):
    """B3 — a deletion is recorded by naming the path in the
    diff. Naming it in the derived set would hand the task a
    file the snapshot cannot contain."""

    def test_a_file_deleted_by_the_diff_is_not_in_the_set(self):
        self._write("keep.py", "k1\n")
        self._write("deleted_by_the_diff.py", "goodbye\n")
        base = self._commit("base")
        self._write("keep.py", "k2\n")
        (self.repo / "deleted_by_the_diff.py").unlink()
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        # Equality is the pin: `not in` against the stub's [] would
        # pass and freeze nothing.
        self.assertEqual(got, ["keep.py"])
        self.assertNotIn("deleted_by_the_diff.py", got)

    def test_a_deletion_whose_hunk_names_a_survivor_keeps_the_survivor_only(
            self):
        # The deleted blob's contents appear in the diff text. A
        # path named there that still exists at ref is B2; the
        # deleted path itself is not.
        self._write("config.toml", "n = 1\n")
        self._write("retired.py", "config.toml\n")
        self._write("keep.py", "k1\n")
        base = self._commit("base")
        self._write("keep.py", "k2\n")
        (self.repo / "retired.py").unlink()
        ref = self._commit("ref")

        got = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(got, ["config.toml", "keep.py"])
        self.assertNotIn("retired.py", got)

    def test_standard_history_drops_the_deleted_path_and_keeps_named_ones(
            self):
        base, ref = _standard_history(self)
        got = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(got, DERIVED)
        self.assertNotIn("deleted_by_the_diff.py", got)
        self.assertNotIn("beta.py", got)
        self.assertNotIn("pkg/mod.py", got)


class DeriveIsDeterministic(_TempRepo):
    """B4 — two callers comparing lists must not have to sort."""

    def test_the_same_inputs_produce_the_same_list(self):
        base, ref = _standard_history(self)
        first = fileset.derive(str(self.repo), ref, base)
        second = fileset.derive(str(self.repo), ref, base)
        self.assertEqual(first, DERIVED)
        self.assertEqual(first, second)


class SnapshotWritesExactlyTheIncludeSet(_TempRepo):
    """B5 — include is a closed set. Derivation extras must not
    leak in, and a bystander must not appear."""

    def test_only_the_named_files_are_written(self):
        _standard_history(self)
        # Deliberately unsorted: the manifest list is sorted, not
        # a replay of the caller's argument order.
        include = ["pkg/mod.py", "beta.py"]
        expected = ["beta.py", "pkg/mod.py"]
        manifest = fileset.snapshot(
            str(self.repo), self._git("rev-parse", "HEAD").stdout.strip(),
            str(self.into), include=include)

        self.assertEqual(manifest.get("files"), expected)
        self.assertEqual(payload_paths(self.into), expected)
        # FILESET.md is metadata about the set, not a member of it.
        self.assertTrue((self.into / "FILESET.md").is_file())
        self.assertEqual(
            all_file_paths(self.into),
            ["FILESET.md", "beta.py", "pkg/mod.py"])

    def test_contents_come_from_the_ref_not_the_worktree(self):
        _, ref = _standard_history(self)
        self._write("alpha.py", "DIRTY\n")
        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into), include=["alpha.py"])
        written = self.into / "alpha.py"
        self.assertTrue(written.is_file())
        self.assertEqual(written.read_text(), "alpha v2\n")
        self.assertEqual(manifest.get("files"), ["alpha.py"])

    def test_nested_paths_keep_their_layout(self):
        _, ref = _standard_history(self)
        fileset.snapshot(
            str(self.repo), ref, str(self.into),
            include=["config/app.toml", "tests/test_alpha.py"])
        self.assertEqual(
            payload_paths(self.into),
            ["config/app.toml", "tests/test_alpha.py"])
        self.assertTrue((self.into / "config" / "app.toml").is_file())


class SnapshotRefusesAMissingInclude(_TempRepo):
    """B6 — silently skipping a missing path would hand the task
    a context it cannot tell from a complete one."""

    def test_a_path_that_never_existed_raises(self):
        _, ref = _standard_history(self)
        with self.assertRaises(fileset.FilesetError):
            fileset.snapshot(
                str(self.repo), ref, str(self.into),
                include=["alpha.py", "no/such/file.py"])

    def test_a_path_deleted_at_ref_raises_even_when_named_in_the_diff(self):
        base, ref = _standard_history(self)
        # The path is in the diff (B3's subject). Asking for it as
        # an include is a request for a blob that is not there.
        with self.assertRaises(fileset.FilesetError):
            fileset.snapshot(
                str(self.repo), ref, str(self.into),
                include=["deleted_by_the_diff.py"], base=base)

    def test_a_worktree_only_file_does_not_count_as_existing_at_ref(self):
        _, ref = _standard_history(self)
        self._write("uncommitted.py", "not at ref\n")
        with self.assertRaises(fileset.FilesetError):
            fileset.snapshot(
                str(self.repo), ref, str(self.into),
                include=["uncommitted.py"])


class SnapshotRefusesAnEmptyIncludeSet(_TempRepo):
    """B7 — an empty snapshot is never a legitimate answer."""

    def test_an_empty_include_list_raises(self):
        _, ref = _standard_history(self)
        with self.assertRaises(fileset.FilesetError):
            fileset.snapshot(
                str(self.repo), ref, str(self.into), include=[])


class SnapshotWholeWritesEveryTrackedFile(_TempRepo):
    """B8 — whole=True is the unpruned tree at ref, not the worktree."""

    def test_every_tracked_file_is_written_and_no_untracked(self):
        _, ref = _standard_history(self)
        self._write("scratch.tmp", "untracked\n")
        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into), whole=True)

        self.assertEqual(manifest.get("files"), TRACKED_AT_REF)
        self.assertEqual(manifest.get("whole"), True)
        self.assertEqual(payload_paths(self.into), TRACKED_AT_REF)
        self.assertNotIn("scratch.tmp", payload_paths(self.into))
        self.assertNotIn("deleted_by_the_diff.py", payload_paths(self.into))


class SnapshotWritesTheDiffIffABaseIsGiven(_TempRepo):
    """B9 — the diff is how a task sees what changed; without a
    base there is no such document, and inventing one would lie."""

    def test_a_base_writes_the_diff_and_names_it_in_the_manifest(self):
        base, ref = _standard_history(self)
        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into),
            include=["alpha.py"], base=base)

        diff_path = self.into / "FILESET.diff"
        self.assertTrue(diff_path.is_file())
        diff_text = diff_path.read_text()
        self.assertIn("alpha.py", diff_text)
        self.assertIn("deleted_by_the_diff.py", diff_text)
        reported = manifest.get("diff")
        self.assertIsNotNone(reported)
        self.assertEqual(Path(reported).resolve(), diff_path.resolve())

    def test_without_a_base_there_is_no_diff_file_and_manifest_says_so(self):
        _, ref = _standard_history(self)
        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into), include=["alpha.py"])

        # The positive half is the snapshot that did get written:
        # asserting only "diff is absent" is green against the stub.
        self.assertTrue((self.into / "alpha.py").is_file())
        self.assertTrue((self.into / "FILESET.md").is_file())
        self.assertFalse((self.into / "FILESET.diff").exists())
        self.assertIsNone(manifest.get("diff"))
        self.assertEqual(
            all_file_paths(self.into), ["FILESET.md", "alpha.py"])


class SnapshotWritesAManifestTheTaskCanRead(_TempRepo):
    """B10 — a task must be able to see what it was and was not given."""

    def test_fileset_md_names_the_ref_the_base_and_every_path(self):
        base, ref = _standard_history(self)
        include = ["alpha.py", "pkg/mod.py"]
        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into),
            include=include, base=base)

        md_path = self.into / "FILESET.md"
        self.assertTrue(md_path.is_file())
        text = md_path.read_text()
        self.assertIn(ref, text)
        self.assertIn(base, text)
        for path in include:
            self.assertIn(path, text)
        self.assertEqual(Path(manifest.get("manifest") or "").resolve(),
                         md_path.resolve())

    def test_the_returned_manifest_carries_the_contract_keys(self):
        base, ref = _standard_history(self)
        into = str(self.into)
        manifest = fileset.snapshot(
            str(self.repo), ref, into,
            include=["zeta.py"], base=base)

        self.assertEqual(
            set(manifest),
            {"ref", "base", "root", "files", "bytes",
             "diff", "manifest", "whole"})
        self.assertEqual(manifest.get("ref"), ref)
        self.assertEqual(manifest.get("base"), base)
        self.assertEqual(Path(manifest.get("root") or "").resolve(),
                         Path(into).resolve())
        self.assertEqual(manifest.get("files"), ["zeta.py"])
        self.assertEqual(manifest.get("whole"), False)


class SnapshotNeverTouchesTheSourceRepo(_TempRepo):
    """B11 — a stray checkout would throw away uncommitted work.
    A clean repo cannot show that damage, so the pin is a dirty
    one."""

    def test_uncommitted_work_survives_and_is_not_what_got_copied(self):
        _, ref = _standard_history(self)
        self._write("alpha.py", "DIRTY VERSION\n")
        self._write("untracked.txt", "do not delete me\n")
        self._write("to_stage.py", "staged new file\n")
        self._git("add", "to_stage.py")
        before = self._fingerprint()

        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into), include=["alpha.py"])

        written = self.into / "alpha.py"
        self.assertTrue(
            written.is_file(),
            "the snapshot must still be produced from a dirty repo")
        self.assertEqual(written.read_text(), "alpha v2\n")
        self.assertNotEqual(written.read_text(), "DIRTY VERSION\n")
        self.assertEqual(manifest.get("files"), ["alpha.py"])
        self.assertEqual(self._fingerprint(), before)

    def test_whole_snapshot_of_a_dirty_repo_still_leaves_it_dirty(self):
        _, ref = _standard_history(self)
        self._write("beta.py", "DIRTY BETA\n")
        self._write("scratch.tmp", "untracked\n")
        before = self._fingerprint()

        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into), whole=True)
        self.assertEqual(manifest.get("files"), TRACKED_AT_REF)
        beta = self.into / "beta.py"
        self.assertTrue(beta.is_file())
        self.assertEqual(beta.read_text(), "unchanged\n")
        self.assertEqual(self._fingerprint(), before)


class ManifestBytesMatchWhatWasWritten(_TempRepo):
    """B12 — a byte count that can disagree with the files is a
    count that lies."""

    def test_bytes_equal_the_sum_of_the_payload_files(self):
        _, ref = _standard_history(self)
        include = ["alpha.py", "config/app.toml", "zeta.py"]
        manifest = fileset.snapshot(
            str(self.repo), ref, str(self.into), include=include)

        written = payload_paths(self.into)
        self.assertEqual(written, include)
        total = sum((self.into / rel).stat().st_size for rel in written)
        self.assertEqual(manifest.get("bytes"), total)
        self.assertGreater(total, 0)


class ParityComparesWholeTreeAgainstPruned(_TempRepo):
    """B13 — the prune is verified by running the same command in
    both trees. True means the answers matched, not that the
    command succeeded."""

    def _pruned(self, ref, include):
        # The stub writes nothing; do not assert on that here or
        # the red lands on snapshot instead of on parity.
        fileset.snapshot(
            str(self.repo), ref, str(self.pruned), include=include)

    def test_matching_results_return_true_and_both_outputs(self):
        _, ref = _standard_history(self)
        self._pruned(ref, ["alpha.py"])
        argv = [sys.executable, "-c",
                "print(open('alpha.py').read(), end='')"]
        ok, full, pruned = fileset.parity(
            str(self.repo), str(self.pruned), argv, ref=ref)
        self.assertEqual(ok, True)
        self.assertEqual(full, "alpha v2\n")
        self.assertEqual(pruned, "alpha v2\n")

    def test_a_mismatch_returns_false_and_the_two_different_outputs(self):
        _, ref = _standard_history(self)
        self._pruned(ref, ["alpha.py"])
        argv = [sys.executable, "-c",
                ("import pathlib; p=pathlib.Path('pkg/mod.py'); "
                 "print(p.read_text() if p.exists() else 'ABSENT')")]
        ok, full, pruned = fileset.parity(
            str(self.repo), str(self.pruned), argv, ref=ref)
        # Stub returns (False, "", ""): False alone would pass.
        # The outputs are the pin.
        self.assertEqual(full.strip(), "x = 1")
        self.assertEqual(pruned.strip(), "ABSENT")
        self.assertEqual(ok, False)

    def test_identical_failures_are_a_match(self):
        # Both trees failing the same way means the prune did not
        # change the answer. That is parity, not an error.
        _, ref = _standard_history(self)
        self._pruned(ref, ["alpha.py"])
        argv = [sys.executable, "-c",
                "import sys; sys.stderr.write('boom\\n'); sys.exit(7)"]
        try:
            ok, full, pruned = fileset.parity(
                str(self.repo), str(self.pruned), argv, ref=ref)
        except fileset.FilesetError:
            self.fail("identical failure must not raise FilesetError")
        self.assertEqual(ok, True)
        self.assertEqual(full, pruned)


class ParityRaisesWhenTheCommandCannotRun(_TempRepo):
    """B14 — 'it failed identically in both' and 'it could not
    run' must not both read as parity."""

    def test_a_command_that_cannot_be_executed_raises(self):
        _, ref = _standard_history(self)
        fileset.snapshot(
            str(self.repo), ref, str(self.pruned), include=["alpha.py"])
        argv = ["/no/such/dir/fileset-no-such-command-7e1c9a3d"]
        with self.assertRaises(fileset.FilesetError):
            fileset.parity(
                str(self.repo), str(self.pruned), argv, ref=ref)

    def test_a_python_that_exits_nonzero_is_not_cannot_run(self):
        # Contrast with the test above: the executable exists. The
        # process starts. That is a result, even when it fails.
        _, ref = _standard_history(self)
        fileset.snapshot(
            str(self.repo), ref, str(self.pruned), include=["alpha.py"])
        argv = [sys.executable, "-c", "raise SystemExit(1)"]
        try:
            result = fileset.parity(
                str(self.repo), str(self.pruned), argv, ref=ref)
        except fileset.FilesetError as exc:
            self.fail(f"a runnable failure raised: {exc}")
        self.assertEqual(result[0], True)
        self.assertEqual(result[1], result[2])


class MainIsACli(_TempRepo):
    """B15 — stdout is the manifest; refusals are 1; usage is 64."""

    def test_snapshot_prints_json_and_returns_zero(self):
        _, ref = _standard_history(self)
        code, out, _ = run_main(
            ["snapshot", ref, "--into", str(self.into),
             "--include", "alpha.py"],
            cwd=self.repo)
        self.assertTrue(out.strip(), "manifest JSON on stdout")
        try:
            manifest = json.loads(out)
        except json.JSONDecodeError:
            self.fail(f"stdout was not JSON: {out!r}")
        self.assertEqual(manifest.get("files"), ["alpha.py"])
        self.assertEqual(manifest.get("ref"), ref)
        self.assertTrue((self.into / "alpha.py").is_file())
        self.assertEqual((self.into / "alpha.py").read_text(), "alpha v2\n")
        self.assertEqual(code, 0)

    def test_base_is_accepted_and_writes_the_diff(self):
        base, ref = _standard_history(self)
        code, out, _err = run_main(
            ["snapshot", ref, "--into", str(self.into),
             "--include", "alpha.py", "--base", base],
            cwd=self.repo)
        self.assertTrue(out.strip())
        try:
            manifest = json.loads(out)
        except json.JSONDecodeError:
            self.fail(f"stdout was not JSON: {out!r}")
        self.assertEqual(code, 0)
        self.assertTrue((self.into / "FILESET.diff").is_file())
        self.assertIsNotNone(manifest.get("diff"))

    def test_whole_writes_the_tracked_tree(self):
        _, ref = _standard_history(self)
        code, out, _err = run_main(
            ["snapshot", ref, "--into", str(self.into), "--whole"],
            cwd=self.repo)
        self.assertTrue(out.strip())
        try:
            manifest = json.loads(out)
        except json.JSONDecodeError:
            self.fail(f"stdout was not JSON: {out!r}")
        self.assertEqual(manifest.get("files"), TRACKED_AT_REF)
        self.assertEqual(manifest.get("whole"), True)
        self.assertEqual(code, 0)

    def test_a_fileset_error_prints_to_stderr_and_returns_one(self):
        _, ref = _standard_history(self)
        code, _out, err = run_main(
            ["snapshot", ref, "--into", str(self.into),
             "--include", "no/such/file.py"],
            cwd=self.repo)
        self.assertEqual(code, 1)
        self.assertTrue(err.strip())

    def test_a_usage_error_returns_sixty_four(self):
        _, ref = _standard_history(self)
        for argv in ([], ["snapshot"], ["nope"],
                     ["snapshot", ref]):
            with self.subTest(argv=argv):
                code, _out, _err = run_main(argv, cwd=self.repo)
                self.assertEqual(code, 64)


if __name__ == "__main__":
    unittest.main()
