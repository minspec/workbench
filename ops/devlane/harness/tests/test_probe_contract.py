"""Contract tests for probe.py.

The behavioural result APIs are deliberately not guessed here: their return
shape is absent from CONTRACT-probe.py.md.  The documented process boundary,
where an unsuccessful arm becomes "no answer", is tested directly.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import support  # noqa: F401  (puts the harness dir on sys.path)

import probe


class ProbeContractTests(unittest.TestCase):
    def test_verdict_tokens_are_three_distinct_states(self):
        verdicts = (probe.CLEAN, probe.LEAKING, probe.INVALID)
        self.assertEqual(("CLEAN", "LEAKING", "INVALID"), verdicts)
        self.assertEqual(3, len(set(verdicts)))

    def test_run_executes_in_the_requested_environment_and_returns_all_streams(self):
        with tempfile.TemporaryDirectory(prefix="probe cwd with spaces ") as temporary:
            cwd = Path(temporary)
            env = dict(os.environ)
            env["PROBE_TEST_SENTINEL"] = "recognisable-environment"
            script = (
                "import os, pathlib, sys; "
                "data = sys.stdin.read(); "
                "pathlib.Path('ran.receipt').write_text(data); "
                "print(os.environ['PROBE_TEST_SENTINEL'] + ':' + data); "
                "print('recognisable-stderr', file=sys.stderr)"
            )

            rc, stdout, stderr = probe._run(
                [sys.executable, "-c", script],
                env,
                cwd,
                stdin="recognisable-stdin",
            )

            self.assertEqual(0, rc)
            self.assertEqual("recognisable-environment:recognisable-stdin\n", stdout)
            self.assertEqual("recognisable-stderr\n", stderr)
            receipt = cwd / "ran.receipt"
            self.assertTrue(receipt.is_file())
            self.assertEqual("recognisable-stdin", receipt.read_text(encoding="utf-8"))

    def test_run_maps_good_looking_output_followed_by_nonzero_to_no_answer(self):
        cases = (
            ("Project Instructions (0)\n", 23),
            ("YES\n", 24),
            ("NO\n", 25),
        )
        self.assertEqual(3, len(cases))
        for apparent_answer, exit_status in cases:
            with self.subTest(apparent_answer=apparent_answer.strip()):
                with tempfile.TemporaryDirectory() as temporary:
                    cwd = Path(temporary)
                    script = (
                        "import pathlib, sys; "
                        f"sys.stdout.write({apparent_answer!r}); "
                        "sys.stdout.flush(); "
                        "pathlib.Path('arm-ran.receipt').write_text('recognisable-arm'); "
                        "sys.stderr.write('teardown failed\\n'); "
                        f"raise SystemExit({exit_status})"
                    )

                    rc, stdout, stderr = probe._run(
                        [sys.executable, "-c", script],
                        dict(os.environ),
                        cwd,
                    )

                    receipt = cwd / "arm-ran.receipt"
                    self.assertTrue(receipt.is_file())
                    self.assertEqual("recognisable-arm", receipt.read_text(encoding="utf-8"))
                    self.assertIsNone(rc)
                    self.assertEqual(apparent_answer, stdout)
                    self.assertEqual("teardown failed\n", stderr)

    def test_run_reports_no_answer_when_the_arm_never_starts(self):
        missing_command = f"probe-command-that-does-not-exist-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as temporary:
            cwd = Path(temporary)
            self.assertEqual(0, len(list(cwd.iterdir())))

            rc, stdout, stderr = probe._run(
                [missing_command],
                dict(os.environ),
                cwd,
            )

            self.assertIsNone(rc)
            self.assertEqual("", stdout)
            self.assertIsInstance(stderr, str)
            self.assertNotEqual("", stderr.strip())
            self.assertEqual(0, len(list(cwd.iterdir())))

    def test_structural_probe_is_read_only_even_with_personal_extras_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_home = Path(temporary) / "operator-codex"
            real_home.mkdir()
            credential = real_home / "auth.json"
            credential.write_text("recognisable-auth\n", encoding="utf-8")
            planted_hook = real_home / "hooks.json"
            planted_hook.write_text("personal-hook\n", encoding="utf-8")
            before = {
                path.relative_to(real_home): (path.is_symlink(), path.read_bytes())
                for path in real_home.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            self.assertEqual(2, len(before))
            self.assertIn(Path("auth.json"), before)
            self.assertIn(Path("hooks.json"), before)
            self.assertEqual(b"personal-hook\n", planted_hook.read_bytes())

            probe.structural("codex", env={"CODEX_HOME": str(real_home)})

            after = {
                path.relative_to(real_home): (path.is_symlink(), path.read_bytes())
                for path in real_home.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            self.assertEqual(before, after)

if __name__ == "__main__":
    unittest.main()
