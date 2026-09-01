"""One clone-shared context stream, from every worktree, for every writer.

Written from `.dev/guide/hooks.md` ("clone-shared context stream",
"every worktree of the clone"), CONTRIB.md ("Every worktree shares one
clone — one … context stream"), and PLAN.md BELONGS item 4: the Claude
writer and the git-native recorders must not land in different files
when git-dir and git-common-dir diverge. Not written from the scripts.

The origin selftest measured the stream via `--git-dir` in a single
checkout, so it could not see the split. These cases run every writer
from a linked worktree's cwd (``.git`` is a gitfile there) and read
the clone stream at `--git-common-dir`.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import support
import test_hooks as hook_contract

STREAM_SH = support.CLAUDE_DIR / "context-stream.sh"
BOUNDARY_SH = support.CLAUDE_DIR / "context-boundary.sh"
KIND_WT = "artifacts"
# Not a substring of worktree names (second-tree, third-tree) or kinds
# the git-native recorders already write (branch, head).
KIND_MAIN = "artifacts-from-primary"
BRANCH_A = "stream-checkout-target"
SINCE_EPOCH = "1970-01-01T00:00:00Z"


class OneStreamPerClone(unittest.TestCase):
    """Pins one stream per clone. Each pinning case must be red at this head."""

    maxDiff = None

    def setUp(self):
        self.assertTrue(
            STREAM_SH.is_file(),
            f"INVALID: context-stream.sh is missing: {STREAM_SH}",
        )
        self.h = hook_contract.HookContractTest(
            "test_h6_second_worktree_appends_to_the_shared_stream"
        )
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def _env(self):
        return self.h.env_for()

    def _linked_clone(self):
        """Primary + two sibling linked worktrees, hooks installed.

        Sibling paths (not nested in the primary) are CONTRIB's shape
        and the input a 'walk up until .git is a directory' resolver
        gets wrong.
        """
        self.h.require_sources()
        repo = self.h.make_repo("primary-clone")
        self.h.git(repo, "branch", "wt-a")
        self.h.git(repo, "branch", "wt-b")
        self.h.git(repo, "branch", BRANCH_A)
        self.h.install(repo)
        wt_a = self.h.tmp / "second-tree"
        wt_b = self.h.tmp / "third-tree"
        self.h.git(repo, "worktree", "add", str(wt_a), "wt-a")
        self.h.git(repo, "worktree", "add", str(wt_b), "wt-b")
        env = self._env()
        support.prove_linked_worktree(repo, wt_a, env, self.h.tmp)
        support.prove_linked_worktree(repo, wt_b, env, self.h.tmp)
        return repo, wt_a, wt_b

    def _git_dir(self, cwd):
        return support.rev_parse_path(cwd, self._env(), "--git-dir", self.h.tmp)

    def _stream_sh(self, cwd, *args, expect=0):
        # Invoke the script itself: prepending bash would hide a broken argv[0].
        self.assertTrue(
            os.access(STREAM_SH, os.X_OK),
            f"INVALID: {STREAM_SH} is not executable",
        )
        return self.h.run_cmd([str(STREAM_SH), *args], cwd, expect=expect)

    def _boundary(self, cwd, payload, expect=0):
        self.assertTrue(
            BOUNDARY_SH.is_file(),
            f"INVALID: context-boundary.sh is missing: {BOUNDARY_SH}",
        )
        self.assertTrue(
            os.access(BOUNDARY_SH, os.X_OK),
            f"INVALID: {BOUNDARY_SH} is not executable",
        )
        proc = support.run_cmd(
            [str(BOUNDARY_SH)], cwd, self._env(), stdin=payload, expect=None
        )
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                f"context-boundary.sh exited {proc.returncode} "
                f"(wanted {expect})\nstdout: {proc.stdout}\n"
                f"stderr: {proc.stderr}",
            )
        return proc

    def _clone_text(self, repo: Path) -> str:
        path = self.h.stream_path(repo)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def _private_stream(self, wt: Path) -> Path:
        return self._git_dir(wt) / hook_contract.STREAM_NAME

    def _wrote_kind_somewhere(self, repo: Path, wt: Path, kind: str) -> None:
        """Plant proof: the writer produced *kind* in at least one stream file."""
        clone = self._clone_text(repo)
        private_path = self._private_stream(wt)
        private = (
            private_path.read_text(encoding="utf-8")
            if private_path.is_file()
            else ""
        )
        self.assertTrue(
            kind in clone or kind in private,
            "INVALID: record did not write "
            f"{kind!r} to the clone stream or the per-worktree git-dir "
            f"(clone {self.h.stream_path(repo)}, private {private_path})",
        )

    def test_record_from_linked_worktree_lands_in_the_clone_stream(self):
        """Claude-side record from a linked worktree appends to the clone stream.

        A script that uses --git-dir, or that uses --git-common-dir only
        when cwd's .git is a directory, writes a private file instead.
        """
        repo, wt_a, _wt_b = self._linked_clone()
        before = self.h.stream_lines(repo)
        proc = self._stream_sh(wt_a, "record", KIND_WT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self._wrote_kind_somewhere(repo, wt_a, KIND_WT)

        after = self.h.stream_lines(repo)
        self.assertEqual(
            after[: len(before)],
            before,
            "clone stream is not append-only after a linked-worktree record",
        )
        added = after[len(before) :]
        self.assertEqual(
            len(added),
            1,
            f"expected one new clone-stream line, got {len(added)}: {added!r}",
        )
        self.assertIn(
            KIND_WT,
            added[0],
            f"clone-stream line does not name the kind: {added[0]!r}",
        )
        self.assertFalse(
            self._private_stream(wt_a).exists(),
            "a private stream appeared in the per-worktree git-dir; "
            "the stream must be shared at the common dir only",
        )
        tail_main = self._stream_sh(repo, "tail", "15").stdout
        self.assertIn(
            KIND_WT,
            tail_main,
            f"tail from the primary checkout missed the linked-worktree "
            f"record: {tail_main!r}",
        )

    def test_tail_from_linked_worktree_sees_a_git_native_commit(self):
        """post-commit writes the clone stream; tail from that worktree sees it.

        A tail that still reads --git-dir shows a different history from
        the worktree than from the primary checkout.
        """
        repo, wt_a, _wt_b = self._linked_clone()
        sha = self.h.commit_change(wt_a, "from-linked\n", "stream-commit-marker")
        clone = self._clone_text(repo)
        self.assertIn(
            sha,
            clone,
            f"INVALID: commit {sha} did not land on the clone stream: {clone!r}",
        )
        self.assertIn(
            sha,
            self._stream_sh(repo, "tail", "15").stdout,
            "INVALID: tail from the primary does not see its own clone stream",
        )
        tail_wt = self._stream_sh(wt_a, "tail", "15").stdout
        self.assertIn(
            sha,
            tail_wt,
            f"tail from the linked worktree missed the git-native commit "
            f"{sha}: {tail_wt!r}",
        )

    def test_tail_from_linked_worktree_sees_a_git_native_checkout(self):
        """post-checkout is the other git-native writer; same promise as commit."""
        repo, wt_a, _wt_b = self._linked_clone()
        self.h.git(wt_a, "checkout", "-q", BRANCH_A)
        clone = self._clone_text(repo)
        self.assertIn(
            BRANCH_A,
            clone,
            f"INVALID: checkout of {BRANCH_A} did not land on the clone "
            f"stream: {clone!r}",
        )
        tail_wt = self._stream_sh(wt_a, "tail", "15").stdout
        self.assertIn(
            BRANCH_A,
            tail_wt,
            f"tail from the linked worktree missed the git-native checkout "
            f"{BRANCH_A}: {tail_wt!r}",
        )

    def test_claude_and_git_writers_are_one_history_from_every_worktree(self):
        """The finding: Claude record + git commit + git checkout, one history.

        Read from the primary, the writing worktree, and a second linked
        worktree. A resolver that is correct only for the main checkout,
        or a tail that merges nothing, fails at least one location.
        """
        repo, wt_a, wt_b = self._linked_clone()
        before = self.h.stream_lines(repo)

        self._stream_sh(wt_a, "record", KIND_WT)
        self._wrote_kind_somewhere(repo, wt_a, KIND_WT)
        sha = self.h.commit_change(wt_a, "mixed\n", "stream-mixed-commit")
        self.h.git(wt_a, "checkout", "-q", BRANCH_A)

        clone = self._clone_text(repo)
        for marker in (KIND_WT, sha, BRANCH_A):
            self.assertIn(
                marker,
                clone,
                f"clone stream is missing {marker!r}: {clone!r}",
            )
        after = self.h.stream_lines(repo)
        self.assertEqual(after[: len(before)], before)
        self.assertGreaterEqual(
            len(after) - len(before),
            3,
            f"three writers should append at least three records, "
            f"observed {after[len(before):]!r}",
        )

        for cwd, label in (
            (repo, "primary"),
            (wt_a, "writing worktree"),
            (wt_b, "second linked worktree"),
        ):
            tail = self._stream_sh(cwd, "tail", "15").stdout
            for marker in (KIND_WT, sha, BRANCH_A):
                self.assertIn(
                    marker,
                    tail,
                    f"tail from {label} ({cwd}) missed {marker!r}: {tail!r}",
                )
        self.assertFalse(
            self._private_stream(wt_a).exists(),
            "Claude-side record left a private stream in the worktree git-dir",
        )
        self.assertFalse(
            self._private_stream(wt_b).exists(),
            "a stream file appeared in the second worktree's private git-dir",
        )

    def test_record_from_main_is_visible_from_every_linked_worktree(self):
        """The other direction: write in the primary, read from the worktrees.

        A tail that uses --git-dir from a linked worktree misses records
        that already land correctly on the common dir.
        """
        repo, wt_a, wt_b = self._linked_clone()
        self._stream_sh(repo, "record", KIND_MAIN)
        clone = self._clone_text(repo)
        self.assertIn(
            KIND_MAIN,
            clone,
            f"INVALID: record from the primary did not land on the clone "
            f"stream: {clone!r}",
        )
        for cwd, label in ((wt_a, "second-tree"), (wt_b, "third-tree")):
            tail = self._stream_sh(cwd, "tail", "15").stdout
            self.assertIn(
                KIND_MAIN,
                tail,
                f"tail from {label} missed a primary-checkout record: {tail!r}",
            )

    def test_kinded_record_without_delta_appends_and_bare_record_stays_quiet(
        self,
    ):
        """Origin selftest, from a linked worktree, against the clone stream.

        Origin measured `--git-dir` in a single checkout: a kinded record
        still appends when nothing in the tree moved, and a bare record
        after that stays silent. Here the same sequence must land on the
        clone-shared file, or the origin proof still cannot see the split.
        """
        repo, wt_a, _wt_b = self._linked_clone()
        self._stream_sh(wt_a, "record")  # seed state, as origin does
        before = self.h.stream_lines(repo)
        self._stream_sh(wt_a, "record", KIND_WT)
        self._wrote_kind_somewhere(repo, wt_a, KIND_WT)
        after = self.h.stream_lines(repo)
        self.assertEqual(after[: len(before)], before)
        added = after[len(before) :]
        self.assertEqual(
            len(added),
            1,
            f"kinded record with no state delta must append one clone-stream "
            f"line, got {added!r}",
        )
        self.assertIn(KIND_WT, added[0])
        tail = self._stream_sh(wt_a, "tail", "1").stdout
        self.assertIn(
            KIND_WT,
            tail,
            f"tail 1 from the linked worktree did not name the kind: {tail!r}",
        )
        tail_main = self._stream_sh(repo, "tail", "1").stdout
        self.assertIn(
            KIND_WT,
            tail_main,
            f"tail 1 from the primary missed the kinded record: {tail_main!r}",
        )
        self._stream_sh(wt_a, "record")
        self.assertEqual(
            self.h.stream_lines(repo),
            after,
            "a bare record with nothing changed must not append again",
        )

    def test_bare_record_from_a_second_worktree_does_not_fork_the_stream(self):
        """A script that shares the log file but keeps per-worktree state still
        forks the history: the second worktree's first bare record looks
        like a first-ever snapshot and appends again. The promise is one
        stream, so a no-kind record after the clone is already seeded
        must stay quiet from every worktree.
        """
        repo, wt_a, wt_b = self._linked_clone()
        self._stream_sh(wt_a, "record")
        self._stream_sh(wt_a, "record", KIND_WT)
        self._wrote_kind_somewhere(repo, wt_a, KIND_WT)
        after = self.h.stream_lines(repo)
        self.assertTrue(
            any(KIND_WT in line for line in after),
            f"kinded record from the first worktree never reached the clone "
            f"stream: {after!r}",
        )
        self._stream_sh(wt_b, "record")
        self.assertEqual(
            self.h.stream_lines(repo),
            after,
            "a bare record from a second worktree appended; the clone "
            "stream is not shared state, only a shared filename",
        )

    def test_since_from_main_sees_a_record_made_in_a_linked_worktree(self):
        """`since` is a third reader. Fixing record and tail is not enough."""
        repo, wt_a, _wt_b = self._linked_clone()
        self._stream_sh(wt_a, "record", KIND_WT)
        self._wrote_kind_somewhere(repo, wt_a, KIND_WT)
        out = self._stream_sh(repo, "since", SINCE_EPOCH).stdout
        self.assertIn(
            KIND_WT,
            out,
            f"since from the primary missed a linked-worktree record: {out!r}",
        )

    def test_since_from_linked_worktree_sees_a_git_native_commit(self):
        """`since` from the worktree must read the clone stream, not git-dir."""
        repo, wt_a, _wt_b = self._linked_clone()
        sha = self.h.commit_change(wt_a, "since-wt\n", "stream-since-commit")
        self.assertIn(
            sha,
            self._clone_text(repo),
            f"INVALID: commit {sha} did not land on the clone stream",
        )
        out = self._stream_sh(wt_a, "since", SINCE_EPOCH).stdout
        self.assertIn(
            sha,
            out,
            f"since from the linked worktree missed the git-native commit "
            f"{sha}: {out!r}",
        )

    def test_boundary_wrapper_from_linked_worktree_appends_to_the_clone_stream(
        self,
    ):
        """The session-facing writer is context-boundary.sh, not the CLI.

        Origin selftest fires the wrapper with `git checkout main`. From a
        linked worktree that fire must append to the clone stream and be
        visible via tail from the primary.
        """
        repo, wt_a, _wt_b = self._linked_clone()
        before = self.h.stream_lines(repo)
        before_text = self._clone_text(repo)
        payload = '{"tool_input":{"command":"git checkout main"}}'
        proc = self._boundary(wt_a, payload, expect=0)
        self.assertTrue(
            support.has_additional_context(proc.stdout),
            "INVALID: boundary wrapper did not fire on git checkout main: "
            f"{proc.stdout!r}",
        )
        after = self.h.stream_lines(repo)
        self.assertEqual(after[: len(before)], before)
        self.assertGreater(
            len(after),
            len(before),
            "boundary wrapper from a linked worktree did not append to the "
            f"clone stream (stdout={proc.stdout!r}, stderr={proc.stderr!r}, "
            f"before={before_text!r}, after={self._clone_text(repo)!r})",
        )
        tail_main = self._stream_sh(repo, "tail", "15").stdout
        tail_wt = self._stream_sh(wt_a, "tail", "15").stdout
        self.assertTrue(
            tail_main.strip(),
            "tail from the primary is empty after a boundary fire",
        )
        # The new clone-stream bytes must appear in both tails. Comparing
        # the added lines themselves, not a formatted wrapper message.
        added = after[len(before) :]
        self.assertGreater(len(added), 0)
        for line in added:
            token = line[:40]
            self.assertTrue(
                token,
                "INVALID: an appended clone-stream line was empty",
            )
            self.assertIn(
                token,
                tail_main,
                f"tail from the primary missed boundary record {token!r}: "
                f"{tail_main!r}",
            )
            self.assertIn(
                token,
                tail_wt,
                f"tail from the linked worktree missed boundary record "
                f"{token!r}: {tail_wt!r}",
            )
        self.assertFalse(
            self._private_stream(wt_a).exists(),
            "boundary fire left a private stream in the worktree git-dir",
        )


class OriginBoundaryNeverBlock(unittest.TestCase):
    """Origin selftest: the PostToolUse wrapper must never block a tool call."""

    def setUp(self):
        self.assertTrue(
            BOUNDARY_SH.is_file(),
            f"INVALID: context-boundary.sh is missing: {BOUNDARY_SH}",
        )
        self.h = hook_contract.HookContractTest(
            "test_h6_second_worktree_appends_to_the_shared_stream"
        )
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.h.require_sources()
        self.repo = self.h.make_repo("primary-clone")

    def test_exits_0_on_malformed_payloads(self):
        payloads = (
            '{"tool_input":{}}',
            "not json",
            "",
            '{"tool_input":{"command":null}}',
        )
        self.assertGreater(len(payloads), 0)
        self.assertTrue(
            os.access(BOUNDARY_SH, os.X_OK),
            f"INVALID: {BOUNDARY_SH} is not executable",
        )
        for payload in payloads:
            with self.subTest(payload=payload[:40]):
                proc = support.run_cmd(
                    [str(BOUNDARY_SH)],
                    self.repo,
                    self.h.env_for(),
                    stdin=payload,
                    expect=None,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"boundary wrapper blocked on {payload!r}: "
                    f"rc={proc.returncode} stderr={proc.stderr!r}",
                )


if __name__ == "__main__":
    unittest.main()
