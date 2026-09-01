"""The commit-msg hook refuses a bad message and never an environment.

Its two paths are opposite by design and both are pinned here, because
either is easy to reverse by accident: refusing when the checker is
absent would stop every commit in a clone that merely lacks it, and
passing a bad message would make the hook decorative.
"""

import subprocess
import unittest

# Imported as a MODULE, not `from test_hooks import HookContractTest`:
# the from-form binds the class into this module's namespace and unittest
# then discovers its 28 cases a second time here (44 -> 72 collected).
import test_hooks

SPLIT = "subject\n\nbody.\n\nSource: original\n\nCo-Authored-By: A <a@x>\n"
JOINED = "subject\n\nbody.\n\nSource: original\nCo-Authored-By: A <a@x>\n"


class TheCommitMsgGate(test_hooks.HookContractTest):
    def prepared(self, with_checker):
        self.require_sources()
        repo = self.make_repo("gate")
        self.install(repo)
        checker = repo / "ops" / "devlane" / "workflow" / "checks" / "commit_trailers.py"
        self.assertTrue(checker.is_file(),
                        "INVALID: staging did not place the checker")
        if not with_checker:
            checker.unlink()
            self.assertFalse(checker.is_file(),
                             "INVALID: the checker was not removed")
        return repo

    def attempt(self, repo, message, content):
        (repo / "f.txt").write_text(content, encoding="utf-8")
        self.assertEqual((repo / "f.txt").read_text(encoding="utf-8"), content,
                         "INVALID: the change did not land")
        msg = repo / "m.txt"
        msg.write_text(message, encoding="utf-8")
        self.assertEqual(msg.read_text(encoding="utf-8"), message,
                         "INVALID: the message did not land")
        self.git(repo, "add", "f.txt")
        proc = subprocess.run(
            ["git", "commit", "-F", str(msg)], cwd=repo,
            capture_output=True, text=True, check=False, env=self.env_for())
        msg.unlink()
        return proc

    def count(self, repo):
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=repo,
                             capture_output=True, text=True, check=False)
        return int(out.stdout.strip() or 0)

    def test_a_split_block_is_refused(self):
        repo = self.prepared(with_checker=True)
        before = self.count(repo)
        proc = self.attempt(repo, SPLIT, "one\n")
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.count(repo), before, "the commit landed anyway")

    def test_a_joined_block_commits(self):
        repo = self.prepared(with_checker=True)
        before = self.count(repo)
        proc = self.attempt(repo, JOINED, "two\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.count(repo), before + 1)

    def test_the_two_messages_differ_only_by_the_blank_line(self):
        """Without this the pair above proves nothing."""
        self.assertEqual(SPLIT.replace("\n\nCo-Authored-By", "\nCo-Authored-By"),
                         JOINED)

    def test_a_missing_checker_warns_and_lets_the_commit_through(self):
        """An environment problem must not stop work; CI is the gate."""
        repo = self.prepared(with_checker=False)
        before = self.count(repo)
        proc = self.attempt(repo, SPLIT, "three\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.count(repo), before + 1)
        self.assertIn("cannot check trailers", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
