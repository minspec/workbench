"""Moved corpus for context-precheck.py, plus D2/D3 reach against real git."""

import shutil
import tempfile
import unittest
from pathlib import Path

import corpus
import support


class PrecheckCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = support.load_claude("context-precheck.py", "context_precheck")

    def test_the_moved_tables_are_the_tables_that_were_there(self):
        self.assertEqual(len(corpus.PRECHECK_STOPS),
                         corpus.COUNTS["PRECHECK_STOPS"])
        self.assertEqual(len(corpus.PRECHECK_PASSES),
                         corpus.COUNTS["PRECHECK_PASSES"])
        self.assertEqual(corpus.COUNTS["PRECHECK_STOPS"], 22)
        self.assertEqual(corpus.COUNTS["PRECHECK_PASSES"], 21)

    def test_gates_each_stop_row(self):
        self.assertGreater(len(corpus.PRECHECK_STOPS), 0)
        for cmd in corpus.PRECHECK_STOPS:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.mod.is_consequential(cmd),
                    f"should gate, stayed silent: {cmd!r}",
                )

    def test_lets_each_pass_row_through(self):
        self.assertGreater(len(corpus.PRECHECK_PASSES), 0)
        for cmd in corpus.PRECHECK_PASSES:
            with self.subTest(cmd=cmd):
                self.assertFalse(
                    self.mod.is_consequential(cmd),
                    f"false stop on {cmd!r}",
                )

    def test_plan_d3_spellings_are_consequential(self):
        rows = [
            "git -C . push origin topic",
            "git -c push.default=simple push origin topic",
            "GIT_SSH_COMMAND='ssh -i k' git push origin topic",
            "gh -R o/r pr create --fill",
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.mod.is_consequential(cmd),
                    f"D3 spelling was not consequential: {cmd!r}",
                )

    def test_plan_d3_data_heredoc_is_not_a_push(self):
        rows = [
            "cat > notes.md <<'EOF'\nWhen ready:\ngit push origin main\nEOF",
            ("git commit -q -F - <<'EOF'\n"
             "hooks: explain what push costs\n\n"
             "git push origin main\nEOF"),
            'git commit -m "wip; git push origin main is next"',
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd[:40]):
                self.assertFalse(
                    self.mod.is_consequential(cmd),
                    f"prose was treated as a push: {cmd!r}",
                )

    def test_plan_d3_executing_heredoc_is_still_a_push(self):
        rows = [
            "bash <<'EOF'\ngit push origin main\nEOF",
            "ssh host bash <<'EOF'\ngit push origin main\nEOF",
        ]
        for cmd in rows:
            with self.subTest(cmd=cmd):
                self.assertTrue(
                    self.mod.is_consequential(cmd),
                    f"executing heredoc was not a push: {cmd!r}",
                )


class PrecheckGitState(unittest.TestCase):
    """D2 and D3 as they actually happen: a clone whose remote has moved."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="precheck-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = support.isolated_env(self.home)
        self.script = support.CLAUDE_DIR / "context-precheck.py"
        self.assertTrue(
            self.script.is_file(),
            f"INVALID: context-precheck.py missing: {self.script}",
        )
        self.fixture = support.StaleClone(self.tmp, self.env).build()

    def _run(self, command: str):
        payload = support.bash_payload(command)
        return support.run_script(
            self.script, payload, self.fixture.clone, self.env
        )

    def _origin_main(self):
        return support.git(
            self.fixture.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()

    def test_d2_missing_fetch_head_with_a_moved_remote_is_a_deny(self):
        """Fresh clone, FETCH_HEAD gone, remote advanced → deny naming origin/main.

        And the fetch must have landed: origin/main equals the remote afterwards.
        """
        clone = self.fixture.clone
        fetch_head = clone / ".git" / "FETCH_HEAD"
        self.assertFalse(
            fetch_head.exists(),
            "INVALID: FETCH_HEAD present before the hook; D2 is the missing case",
        )
        self.assertEqual(self._origin_main(), self.fixture.old_sha)
        self.assertNotEqual(self.fixture.old_sha, self.fixture.new_sha)

        proc = self._run("git push origin topic")
        decision, reason = support.permission_decision(proc.stdout)
        self.assertEqual(
            decision, "deny",
            f"D2 wanted deny, got {decision!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn(
            "origin/main", reason,
            f"deny did not name origin/main: {reason!r}",
        )
        self.assertTrue(
            fetch_head.is_file(),
            "INVALID or red: fetch did not write FETCH_HEAD",
        )
        self.assertEqual(
            self._origin_main(), self.fixture.new_sha,
            "refs were not refreshed; origin/main still stale",
        )

    def test_d3_spellings_deny_and_refresh_against_the_same_moved_remote(self):
        spellings = [
            "git -C . push origin topic",
            "git -c push.default=simple push origin topic",
            "GIT_SSH_COMMAND='ssh -i k' git push origin topic",
            "gh -R o/r pr create --fill",
        ]
        clone = self.fixture.clone
        fetch_head = clone / ".git" / "FETCH_HEAD"
        for cmd in spellings:
            with self.subTest(cmd=cmd):
                self.fixture.restale()
                self.assertFalse(
                    fetch_head.exists(),
                    f"INVALID: FETCH_HEAD present before {cmd!r}",
                )
                self.assertEqual(self._origin_main(), self.fixture.old_sha)

                proc = self._run(cmd)
                decision, reason = support.permission_decision(proc.stdout)
                self.assertEqual(
                    decision, "deny",
                    f"D3 spelling {cmd!r} wanted deny, got {decision!r}\n"
                    f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
                )
                self.assertIn(
                    "origin/main", reason,
                    f"{cmd!r} deny did not name origin/main: {reason!r}",
                )
                self.assertEqual(
                    self._origin_main(), self.fixture.new_sha,
                    f"{cmd!r} left origin/main stale",
                )
