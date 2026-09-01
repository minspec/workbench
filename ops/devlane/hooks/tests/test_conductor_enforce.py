"""Policy corpus and process boundary for conductor-enforce.py."""

import json
import os
import unittest

import support


class ConductorPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = support.load_claude("conductor-enforce.py", "conductor_enforce")

    @staticmethod
    def bash(command):
        return json.loads(support.bash_payload(command))

    def test_agent_is_denied(self):
        self.assertEqual(
            self.mod.deny_reason({"tool_name": "Agent"}),
            self.mod.AGENT_REASON,
        )

    def test_each_denied_git_subcommand_is_denied(self):
        for subcommand in self.mod.POLICY["deny_git"]:
            with self.subTest(subcommand=subcommand):
                self.assertEqual(
                    self.mod.deny_reason(self.bash(f"git {subcommand}")),
                    self.mod.INVESTIGATION_REASON,
                )

    def test_each_allowed_git_subcommand_is_allowed(self):
        for subcommand in self.mod.POLICY["allow_git"]:
            with self.subTest(subcommand=subcommand):
                self.assertIsNone(
                    self.mod.deny_reason(self.bash(f"git {subcommand}"))
                )

    def test_structured_pr_view_is_denied_but_plain_view_is_allowed(self):
        self.assertEqual(
            self.mod.deny_reason(self.bash("gh pr view --json state")),
            self.mod.INVESTIGATION_REASON,
        )
        self.assertIsNone(
            self.mod.deny_reason(self.bash("gh pr view 64"))
        )

    def test_gh_api_is_denied(self):
        self.assertEqual(
            self.mod.deny_reason(self.bash("gh api repos/o/r")),
            self.mod.INVESTIGATION_REASON,
        )

    def test_each_lever_anywhere_overrides_a_denied_git_subcommand(self):
        for lever in self.mod.POLICY["allow_anywhere"]:
            with self.subTest(lever=lever):
                self.assertIsNone(
                    self.mod.deny_reason(
                        self.bash(f"git log --oneline && {lever} task")
                    )
                )

    def test_fable_dispatch_is_allowed_with_a_denied_git_subcommand(self):
        self.assertIsNone(
            self.mod.deny_reason(
                self.bash("fable-dispatch.sh plan && git diff")
            )
        )

    def test_grep_over_a_tracked_file_is_denied(self):
        self.assertEqual(
            self.mod.deny_reason(
                self.bash("grep conductor AGENTS.md")
            ),
            self.mod.INVESTIGATION_REASON,
        )

    def test_cat_over_a_job_path_is_allowed(self):
        self.assertIsNone(
            self.mod.deny_reason(
                self.bash("cat /tmp/scratchpad/jobs/123/result.txt")
            )
        )

    def test_unrelated_tool_is_allowed(self):
        self.assertIsNone(self.mod.deny_reason({"tool_name": "Read"}))


class ConductorProcess(unittest.TestCase):
    def test_malformed_stdin_exits_zero_and_prints_nothing(self):
        script = support.CLAUDE_DIR / "conductor-enforce.py"
        proc = support.run_script(
            script, "{not json", support.WORKTREE_ROOT, os.environ.copy()
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
