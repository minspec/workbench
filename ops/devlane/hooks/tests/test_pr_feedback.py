"""Tests for pr-feedback.sh, written from PLAN D1 and the origin corpus.

The origin test (pr-feedback-test.sh) proved --watch notices each of four
surfaces when a stub `gh` serves fixtures through real jq. It never
covered a `gh` that fails, which is why D1 (error printed as `(none)`,
exit 0) survived it. These cases port that corpus and add the failure,
watch-fingerprint, and reviewThreads paging pins.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import support

SCRIPT = support.CLAUDE_DIR / "pr-feedback.sh"
STUB_SRC = Path(__file__).resolve().parent / "gh_stub.py"

HEADINGS = (
    "conversation comments",
    "reviews",
    "inline review comments",
    "reactions on comments",
    "unresolved threads",
)

CONV_BASE = (
    '[{"id":1,"updated_at":"T1","created_at":"2026-08-14T18:00:00Z",'
    '"user":{"login":"xormania"},"body":"@codex review"}]'
)
REV_BASE = (
    '[{"id":10,"submitted_at":"2026-08-14T18:09:00Z","state":"COMMENTED",'
    '"user":{"login":"bot"},"body":"review"}]'
)
INLINE_BASE = (
    '[{"id":20,"updated_at":"T1","created_at":"2026-08-14T18:09:00Z",'
    '"user":{"login":"bot"},"path":"a.py","line":5,"body":"finding"}]'
)
THREADS_EMPTY = (
    '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[]}}}}}'
)
EMPTY_LIST = "[]"

CONV_MOVED = (
    '[{"id":1,"updated_at":"T1","created_at":"2026-08-14T18:00:00Z",'
    '"user":{"login":"xormania"},"body":"@codex review"},'
    '{"id":2,"updated_at":"T2","created_at":"2026-08-14T18:43:00Z",'
    '"user":{"login":"bot"},"body":"VERDICT-AS-CONV-UNIQUE"}]'
)
INLINE_MOVED = (
    '[{"id":20,"updated_at":"T1","created_at":"2026-08-14T18:09:00Z",'
    '"user":{"login":"bot"},"path":"a.py","line":5,"body":"finding"},'
    '{"id":21,"updated_at":"T2","created_at":"2026-08-14T18:44:00Z",'
    '"user":{"login":"bot"},"path":"b.py","line":9,'
    '"body":"INLINE-FINDING-UNIQUE"}]'
)
REV_MOVED = (
    '[{"id":10,"submitted_at":"2026-08-14T18:09:00Z","state":"COMMENTED",'
    '"user":{"login":"bot"},"body":"review"},'
    '{"id":11,"submitted_at":"2026-08-14T18:45:00Z","state":"APPROVED",'
    '"user":{"login":"human"},"body":"REVIEW-APPROVED-UNIQUE"}]'
)
REACT_MOVED = (
    '[{"content":"+1","user":{"login":"react-bot-unique"}}]'
)
THREADS_MOVED = json.dumps({
    "data": {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "nodes": [
                        {
                            "isResolved": False,
                            "path": "thread-moved-unique.py",
                            "line": 3,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "THREAD-MOVED-UNIQUE",
                                        "author": {"login": "bot"},
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
        }
    }
})


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.wait(timeout=3)


class PrFeedback(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        support.require_jq()
        self.assertTrue(
            SCRIPT.is_file(),
            f"INVALID: pr-feedback.sh missing: {SCRIPT}",
        )
        self.assertTrue(
            STUB_SRC.is_file(),
            f"INVALID: gh stub source missing: {STUB_SRC}",
        )
        self.tmp = Path(tempfile.mkdtemp(prefix="pr-feedback-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.fix = self.tmp / "fix"
        self.fix.mkdir()
        self.mode_file = self.tmp / "gh-mode"
        support.plant_text(self.mode_file, "ok\n", recognisable="ok")
        stub_body = STUB_SRC.read_text(encoding="utf-8")
        self.stub = support.plant_executable(self.bin / "gh", stub_body)
        which = shutil.which("gh", path=str(self.bin) + os.pathsep
                             + os.environ.get("PATH", ""))
        self.assertEqual(
            Path(which).resolve(), self.stub.resolve(),
            f"INVALID: which gh is {which}, not the stub {self.stub}",
        )

    def _env(self, extra=None):
        merged = {
            "GH_FIXTURES": str(self.fix),
            "GH_MODE_FILE": str(self.mode_file),
        }
        if extra:
            merged.update(extra)
        return support.isolated_env(
            self.home, extra_path=self.bin, extra=merged,
        )

    def _plant_file(self, name: str, content: str, *, recognisable: str):
        path = self.fix / name
        support.plant_text(path, content, recognisable=recognisable)
        landed = path.read_text(encoding="utf-8")
        self.assertEqual(landed, content, f"INVALID: {name} drifted")
        return path

    def _baseline(self):
        self._plant_file("conv.json", CONV_BASE, recognisable="@codex review")
        self._plant_file("reviews.json", REV_BASE, recognisable='"id":10')
        self._plant_file(
            "inline.json", INLINE_BASE, recognisable='"path":"a.py"',
        )
        self._plant_file("react-1.json", EMPTY_LIST, recognisable="[")
        self._plant_file("empty.json", EMPTY_LIST, recognisable="[")
        self._plant_file(
            "threads.json", THREADS_EMPTY, recognisable="reviewThreads",
        )

    def _empty_success(self):
        self._plant_file("conv.json", EMPTY_LIST, recognisable="[")
        self._plant_file("reviews.json", EMPTY_LIST, recognisable="[")
        self._plant_file("inline.json", EMPTY_LIST, recognisable="[")
        self._plant_file("react-1.json", EMPTY_LIST, recognisable="[")
        self._plant_file("empty.json", EMPTY_LIST, recognisable="[")
        self._plant_file(
            "threads.json", THREADS_EMPTY, recognisable="reviewThreads",
        )

    def _move(self, name: str, new_content: str, recognisable: str):
        path = self.fix / name
        self.assertTrue(path.is_file(), f"INVALID: {name} missing before move")
        before = path.read_bytes()
        self.assertTrue(before, f"INVALID: {name} empty before move")
        before_len = len(before)
        support.plant_text(path, new_content, recognisable=recognisable)
        after = path.read_bytes()
        if after == before:
            raise support.PlantFailed(
                f"INVALID: FIXTURE DID NOT MOVE — {name}"
            )
        if len(after) < max(1, before_len // 2):
            raise support.PlantFailed(
                f"INVALID: FIXTURE CLOBBERED — {name} "
                f"({before_len} -> {len(after)} bytes)"
            )

    def _prove_stub_ok(self, env):
        conv = support.run_cmd(
            ["gh", "api", "repos/o/r/issues/13/comments", "--jq", "length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(
            conv.stdout.strip(), "1",
            f"INVALID: stub conv length {conv.stdout!r}",
        )
        inline = support.run_cmd(
            ["gh", "api", "repos/o/r/pulls/13/comments", "--jq", "length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(
            inline.stdout.strip(), "1",
            f"INVALID: stub inline length {inline.stdout!r} "
            "(did not distinguish inline from conversation)",
        )
        paged = support.run_cmd(
            ["gh", "api",
             "repos/o/r/issues/13/comments?per_page=100", "--jq", "length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(
            paged.stdout.strip(), "1",
            f"INVALID: stub ignored ?per_page=100: {paged.stdout!r}",
        )

    def _prove_stub_fails(self, env, *, rc=1, stderr_snip="authentication"):
        proc = support.run_cmd(
            ["gh", "api", "repos/o/r/issues/13/comments"],
            self.tmp, env, expect=None,
        )
        self.assertEqual(
            proc.returncode, rc,
            f"INVALID: stub fail rc {proc.returncode}, wanted {rc}",
        )
        if stderr_snip:
            self.assertIn(
                stderr_snip, proc.stderr,
                f"INVALID: stub stderr {proc.stderr!r}",
            )
        else:
            self.assertEqual(
                proc.stderr.strip(), "",
                f"INVALID: stub stderr not empty: {proc.stderr!r}",
            )

    def _report(self, env, *args):
        argv = ["bash", str(SCRIPT), "13", "o/r", *args]
        return support.run_cmd(argv, self.tmp, env, expect=None)

    def _watch(self, env, *, interval=1, timeout=8, mover=None):
        argv = [
            "bash", str(SCRIPT), "13", "o/r",
            "--watch", "--interval", str(interval), "--timeout", str(timeout),
        ]
        proc = subprocess.Popen(
            argv,
            cwd=str(self.tmp),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.addCleanup(_kill, proc)
        if mover is not None:
            time.sleep(2)
            mover()
        try:
            out, _ = proc.communicate(timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            _kill(proc)
            out = proc.stdout.read() if proc.stdout else ""
            self.fail(
                f"watch did not exit within {timeout + 5}s; out={out!r}"
            )
        return proc.returncode, out or ""

    def _combined(self, proc):
        return (proc.stdout or "") + (proc.stderr or "")

    def _section(self, out: str, heading: str) -> str:
        idx = out.lower().find(heading.lower())
        self.assertGreaterEqual(
            idx, 0, f"heading {heading!r} missing from:\n{out}",
        )
        rest = out[idx + len(heading):]
        next_idx = len(rest)
        for other in HEADINGS:
            if other.lower() == heading.lower():
                continue
            found = rest.lower().find(other.lower())
            if 0 <= found < next_idx:
                next_idx = found
        return rest[:next_idx]

    # --- origin corpus: the stub itself --------------------------------

    def test_stub_serves_conversation_and_distinguishes_inline(self):
        self._baseline()
        env = self._env()
        self._prove_stub_ok(env)

    # --- origin corpus: one-shot report --------------------------------

    def test_one_shot_lists_the_five_surfaces(self):
        self._baseline()
        env = self._env()
        self._prove_stub_ok(env)
        proc = self._report(env)
        out = self._combined(proc)
        for heading in HEADINGS:
            with self.subTest(heading=heading):
                self.assertIn(
                    heading, out.lower(),
                    f"report omitted {heading!r}:\n{out}",
                )
        self.assertIn(
            "@codex review", out,
            f"INVALID: baseline conversation never reached the report:\n{out}",
        )

    def test_one_shot_empty_is_none_and_exit_0(self):
        self._empty_success()
        env = self._env()
        proc = support.run_cmd(
            ["gh", "api", "repos/o/r/issues/13/comments", "--jq", "length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(
            proc.stdout.strip(), "0",
            f"INVALID: empty fixture length {proc.stdout!r}",
        )
        report = self._report(env)
        out = self._combined(report)
        self.assertEqual(
            report.returncode, 0,
            f"empty success wanted rc 0, got {report.returncode}\n{out}",
        )
        for heading in (
            "conversation comments",
            "reviews",
            "inline review comments",
            "reactions on comments",
        ):
            body = self._section(out, heading)
            self.assertIn(
                "(none)", body,
                f"{heading} empty body was not (none): {body!r}",
            )
            self.assertNotIn("UNREADABLE", body)

    # --- origin corpus: --watch notices each surface -------------------

    def _assert_watch_notices(self, name, new, unique, label, recognisable):
        self._baseline()
        env = self._env()
        self._prove_stub_ok(env)

        def mover():
            self._move(name, new, recognisable)

        rc, out = self._watch(env, interval=1, timeout=10, mover=mover)
        self.assertEqual(
            rc, 0,
            f"--watch did not notice {label} (rc={rc}):\n{out}",
        )
        named = re.search(
            rf"changed after[^\n]*{re.escape(label)}", out, re.IGNORECASE,
        )
        self.assertTrue(
            unique in out or named,
            f"--watch fired but did not name {label!r} or "
            f"include {unique!r}:\n{out}",
        )

    def test_watch_notices_conversation_comment(self):
        self._assert_watch_notices(
            "conv.json", CONV_MOVED, "VERDICT-AS-CONV-UNIQUE",
            "conv", "VERDICT-AS-CONV-UNIQUE",
        )

    def test_watch_notices_inline_comment(self):
        self._assert_watch_notices(
            "inline.json", INLINE_MOVED, "INLINE-FINDING-UNIQUE",
            "inline", "INLINE-FINDING-UNIQUE",
        )

    def test_watch_notices_review_body(self):
        self._assert_watch_notices(
            "reviews.json", REV_MOVED, "REVIEW-APPROVED-UNIQUE",
            "rev", "REVIEW-APPROVED-UNIQUE",
        )

    def test_watch_notices_reaction(self):
        self._assert_watch_notices(
            "react-1.json", REACT_MOVED, "react-bot-unique",
            "react", "react-bot-unique",
        )

    def test_watch_notices_unresolved_thread(self):
        """Fifth surface. Origin grepped four endpoint names and omitted it."""
        self._assert_watch_notices(
            "threads.json", THREADS_MOVED, "thread-moved-unique.py",
            "thread", "THREAD-MOVED-UNIQUE",
        )

    def test_watch_times_out_without_change_exit_1(self):
        self._baseline()
        env = self._env()
        self._prove_stub_ok(env)
        before = (self.fix / "conv.json").read_bytes()
        rc, out = self._watch(env, interval=1, timeout=3, mover=None)
        self.assertEqual(
            rc, 1,
            f"silent watch wanted rc 1, got {rc}:\n{out}",
        )
        self.assertNotRegex(out, r"changed after")
        after = (self.fix / "conv.json").read_bytes()
        self.assertEqual(
            after, before,
            "INVALID: watching mutated the fixtures",
        )

    def test_watch_does_not_mutate_fixtures(self):
        self._baseline()
        env = self._env()
        checksums = {
            n: (self.fix / n).read_bytes()
            for n in (
                "conv.json", "reviews.json", "inline.json",
                "react-1.json", "threads.json",
            )
        }
        self._report(env)
        rc, _ = self._watch(env, interval=1, timeout=3, mover=None)
        self.assertEqual(rc, 1)
        for name, before in checksums.items():
            landed = (self.fix / name).read_bytes()
            self.assertEqual(
                landed, before,
                f"watching/report mutated {name}",
            )

    # --- D1: a failed gh is not an empty PR ----------------------------

    def test_failing_gh_exits_nonzero(self):
        self._empty_success()
        env = self._env(extra={"GH_FAIL": "1"})
        self._prove_stub_fails(env)
        proc = self._report(env)
        out = self._combined(proc)
        self.assertNotEqual(
            proc.returncode, 0,
            f"failed gh reported as success rc=0:\n{out}",
        )

    def test_failing_gh_marks_surfaces_unreadable_not_none(self):
        self._empty_success()
        env = self._env(extra={"GH_FAIL": "1"})
        self._prove_stub_fails(env)
        proc = self._report(env)
        out = self._combined(proc)
        for heading in (
            "conversation comments",
            "reviews",
            "inline review comments",
            "reactions on comments",
        ):
            with self.subTest(heading=heading):
                body = self._section(out, heading)
                self.assertRegex(
                    body,
                    r"UNREADABLE",
                    f"{heading} did not say UNREADABLE:\n{body}",
                )
                self.assertRegex(
                    body,
                    r"exit 1",
                    f"{heading} did not name gh exit 1:\n{body}",
                )
                self.assertIn(
                    "authentication failed", body,
                    f"{heading} dropped the stderr line:\n{body}",
                )
                self.assertNotIn(
                    "(none)", body,
                    f"{heading} printed (none) for an unread surface:\n{body}",
                )

    def test_failing_gh_output_differs_from_empty(self):
        self._empty_success()
        empty_env = self._env()
        empty = self._report(empty_env)
        fail_env = self._env(extra={"GH_FAIL": "1"})
        self._prove_stub_fails(fail_env)
        fail = self._report(fail_env)
        empty_out = self._combined(empty)
        fail_out = self._combined(fail)
        self.assertNotEqual(
            fail_out, empty_out,
            "auth failure and genuinely empty PR were byte-identical",
        )
        self.assertEqual(empty.returncode, 0, empty_out)
        self.assertNotEqual(fail.returncode, 0, fail_out)

    def test_failing_gh_empty_stderr_is_still_unreadable(self):
        self._empty_success()
        env = self._env(extra={"GH_FAIL": "1", "GH_FAIL_STDERR": ""})
        self._prove_stub_fails(env, stderr_snip="")
        proc = self._report(env)
        out = self._combined(proc)
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("UNREADABLE", out, out)
        body = self._section(out, "conversation comments")
        self.assertNotIn("(none)", body, body)

    def test_one_surface_failing_keeps_the_others_readable(self):
        self._baseline()
        env = self._env(extra={"GH_FAIL_ENDPOINT": "/reviews"})
        ok = support.run_cmd(
            ["gh", "api", "repos/o/r/issues/13/comments", "--jq", "length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(ok.stdout.strip(), "1", "INVALID: conv should work")
        bad = support.run_cmd(
            ["gh", "api", "repos/o/r/pulls/13/reviews"],
            self.tmp, env, expect=None,
        )
        self.assertEqual(
            bad.returncode, 1,
            f"INVALID: reviews endpoint did not fail: {bad.returncode}",
        )
        proc = self._report(env)
        out = self._combined(proc)
        self.assertNotEqual(proc.returncode, 0, out)
        conv = self._section(out, "conversation comments")
        self.assertIn("@codex review", conv, conv)
        self.assertNotIn("UNREADABLE", conv, conv)
        reviews = self._section(out, "reviews")
        self.assertIn("UNREADABLE", reviews, reviews)
        self.assertNotIn("(none)", reviews, reviews)

    # --- D1: --watch must not fingerprint a failed call ----------------

    def test_watch_does_not_treat_failed_poll_as_a_change(self):
        self._baseline()
        env = self._env()
        self._prove_stub_ok(env)

        def flip_to_fail():
            calls = self.fix / "callcount"
            self.assertTrue(
                calls.is_file() and int(calls.read_text().strip() or "0") > 0,
                "INVALID: watch never called gh before the failure plant",
            )
            support.plant_text(self.mode_file, "fail\n", recognisable="fail")
            landed = self.mode_file.read_text(encoding="utf-8").strip()
            self.assertEqual(landed, "fail", "INVALID: mode did not flip")
            self._prove_stub_fails(env)

        rc, out = self._watch(env, interval=1, timeout=6, mover=flip_to_fail)
        self.assertNotEqual(
            rc, 0,
            f"failed poll exited 0 as if something changed:\n{out}",
        )
        self.assertNotRegex(
            out, r"changed after",
            f"failed poll was reported as a change:\n{out}",
        )

    def test_watch_failed_poll_exit_is_not_success_or_timeout(self):
        """Watch timeout is 1; an unread poll must use a different nonzero."""
        self._baseline()
        env = self._env()
        self._prove_stub_ok(env)

        def flip_to_fail():
            support.plant_text(self.mode_file, "fail\n", recognisable="fail")
            self._prove_stub_fails(env)

        rc, out = self._watch(env, interval=1, timeout=6, mover=flip_to_fail)
        self.assertNotIn(
            rc, (0, 1),
            f"failed poll rc={rc} collides with success(0) or "
            f"timeout(1):\n{out}",
        )
        self.assertRegex(
            out, r"UNREADABLE|error|fail",
            f"failed poll was silent about the error:\n{out}",
        )

    def test_watch_failed_poll_on_empty_baseline_is_not_timeout(self):
        self._empty_success()
        env = self._env(extra={"GH_FAIL": "1"})
        self._prove_stub_fails(env)
        rc, out = self._watch(env, interval=1, timeout=4, mover=None)
        self.assertNotEqual(rc, 0, out)
        self.assertNotEqual(
            rc, 1,
            f"failed empty baseline looked like a quiet timeout:\n{out}",
        )
        self.assertNotRegex(out, r"changed after")

    # --- D1: reviewThreads must not silently stop at first:50 ----------

    def _assert_thread_visible(self, env, n, token):
        proc = self._report(env)
        out = self._combined(proc)
        self.assertIn(
            token, out,
            f"unresolved thread {n} ({token}) was dropped:\n{out}",
        )

    def test_unresolved_thread_past_first_50_is_shown(self):
        self._empty_success()
        env = self._env(extra={"GH_THREAD_COUNT": "51"})
        sample = support.run_cmd(
            ["gh", "api", "graphql", "-f",
             "query=reviewThreads(first: 50)", "--jq",
             ".data.repository.pullRequest.reviewThreads.nodes | length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(
            sample.stdout.strip(), "50",
            f"INVALID: first:50 page length {sample.stdout!r}",
        )
        page2 = support.run_cmd(
            ["gh", "api", "graphql", "-f",
             "query=reviewThreads(first: 50)", "-f", "threadCursor=c50",
             "--jq",
             ".data.repository.pullRequest.reviewThreads.nodes[0].path"],
            self.tmp, env, expect=0,
        )
        self.assertIn(
            "thread-51-unique.py", page2.stdout,
            f"INVALID: page 2 did not serve thread 51: {page2.stdout!r}",
        )
        self._assert_thread_visible(env, 51, "thread-51-unique.py")

    def test_unresolved_thread_past_first_100_is_shown(self):
        """first:100 without paging is the pr-overview mutant, copied here."""
        self._empty_success()
        env = self._env(extra={"GH_THREAD_COUNT": "101"})
        sample = support.run_cmd(
            ["gh", "api", "graphql", "-f",
             "query=reviewThreads(first: 100)", "--jq",
             ".data.repository.pullRequest.reviewThreads.nodes | length"],
            self.tmp, env, expect=0,
        )
        self.assertEqual(
            sample.stdout.strip(), "100",
            f"INVALID: first:100 page length {sample.stdout!r}",
        )
        self._assert_thread_visible(env, 101, "thread-101-unique.py")
