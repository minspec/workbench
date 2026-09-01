"""The probe reports INVALID when an arm did not run.

This app's whole argument is that "nobody looked" must never print the
same way as "we checked and it was fine", and the probe is where that
is decided: a harness that died before loading anything shows no leak,
and reading that as isolation working is a test proving its own setup
never happened.
"""

import importlib.util
import unittest
from unittest import mock
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]

#: What `grok inspect` prints when the operator's files ARE loaded, and
#: when none are. The second is a WELL-FORMED answer, which is exactly
#: why a nonzero exit beside it is dangerous.
LEAKING = ("Project Instructions (2)\n"
           "├ /home/op/.claude/CLAUDE.md\n"
           "└ /home/op/rules.md\n")
NOTHING = "Project Instructions (0)\n└ (none)\n"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HARNESS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnArmThatDidNotRunIsNotAnAnswer(unittest.TestCase):
    def setUp(self):
        self.probe = load("probe")
        # `load` gives a fresh MODULE, but `probe.subprocess` and
        # `probe.isolation` are the same objects every other test in the
        # process holds. Assigning through them mutates global state and
        # never puts it back: these tests passed alone and broke an
        # independent author's the moment both ran in one discovery,
        # which is the only way anyone would have noticed.
        self.enterContext(
            mock.patch.object(
                self.probe.isolation, "isolated",
                lambda harness, root, env=None: ({"HOME": root}, [])))

    def verdict(self, isolated_exit, isolated_out=NOTHING):
        """Run both arms; the unisolated one always succeeds and leaks.

        The PROCESS is faked, not `_run`. The nonzero-exit rule lives
        inside `_run` now — deliberately, so a new arm cannot be written
        without it — and a test that replaces `_run` would step over the
        very rule it is checking and pass either way.
        """
        calls = {"n": 0}

        class Done:
            def __init__(self, rc, out, err):
                self.returncode, self.stdout, self.stderr = rc, out, err

        def fake(argv, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return Done(0, LEAKING, "")
            if isolated_exit is None:
                raise OSError("the harness could not be started")
            return Done(isolated_exit, isolated_out,
                        "grok: could not read config")

        self.enterContext(
            mock.patch.object(self.probe.subprocess, "run", fake))
        result = self.probe.behavioural_grok(env={})
        self.assertEqual(calls["n"], 2, "both arms must have been attempted")
        return result

    def test_a_nonzero_exit_is_invalid_even_when_stdout_parses(self):
        # A grok that failed to initialise still prints a well-formed
        # `Project Instructions (0)` header, and taking that as an
        # answer certified isolation off a broken probe.
        self.assertEqual(self.verdict(1).verdict, self.probe.INVALID)

    def test_a_clean_run_still_reports_clean(self):
        # The twin. A guard that turned a working probe into INVALID
        # would trade a false pass for a false alarm, and the only
        # free isolation check we have would stop being run.
        self.assertEqual(self.verdict(0).verdict, self.probe.CLEAN)

    def test_a_leak_is_still_reported_as_leaking(self):
        self.assertEqual(self.verdict(0, LEAKING).verdict, self.probe.LEAKING)

    def test_the_claude_arm_refuses_a_nonzero_exit_too(self):
        # The rule was fixed in the grok arm and left in this one, and
        # the arm that was missed is the one the next reviewer found
        # (Codex, PR #40, twice). It lives in `_run` now, so a third arm
        # cannot be written without it.
        class Done:
            def __init__(self, rc, out):
                self.returncode, self.stdout, self.stderr = rc, out, ""

        for exit_code, want in ((1, self.probe.INVALID),
                                (0, self.probe.CLEAN)):
            with self.subTest(exit_code=exit_code):
                calls = {"n": 0}

                def fake(argv, exit_code=exit_code, calls=calls, **kwargs):
                    calls["n"] += 1
                    return Done(exit_code, "YES" if calls["n"] == 1 else "NO")

                self.enterContext(
                    mock.patch.object(self.probe.subprocess, "run", fake))
                self.assertEqual(
                    self.probe.behavioural_claude(env={}).verdict, want)

    def test_a_transport_failure_is_invalid(self):
        # `_run` answers None for an OSError or a timeout; the same
        # branch has to cover it.
        self.assertEqual(self.verdict(None).verdict, self.probe.INVALID)


if __name__ == "__main__":
    unittest.main()
