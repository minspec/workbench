"""Origin test-guard-hook-test.sh: the PostToolUse wrapper's never-block contract.

The scanner itself is already in test_test_guard.py. Origin also tested the
wrapper: a real Write/Edit payload reaches it, a finding comes back as
additionalContext, and — the property that matters more than detection —
it NEVER blocks a tool call, whatever it is handed. This suite did not
have that.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import support

HOOK = support.CLAUDE_DIR / "test-guard-hook.sh"

JUNK = (
    '{"tool_input":{}}',
    "not json at all",
    "",
    '{"tool_input":{"file_path":null}}',
    '{"tool_name":"Write"}',
    '{"tool_name":"Write","tool_input":{"file_path":"/no/such/file.sh"}}',
    '{"tool_name":"Write","tool_input":{"file_path":"/etc/shadow"}}',
)

UNGUARDED = '''clean; sed -i "s/^## Checklist$/## Checklis/" "$F"
[ "$(fired checklist)" != "0" ] && ok "catches it" || bad "catches it"
'''

GUARDED = '''plant() {
  before=$(cksum < "$F"); "$1"; after=$(cksum < "$F")
  [ "$before" = "$after" ] && { bad "PLANT DID NOT APPLY"; return 1; }
}
clean; plant mut && sed -i "s/a/b/" "$F"
'''

NOT_A_TEST = '''sed -i "s/^version = .*/version = \\"1.2\\"/" pyproject.toml
'''


class TestGuardHookWrapper(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HOOK.is_file(), f"INVALID: wrapper missing: {HOOK}")
        self.assertTrue(
            os.access(HOOK, os.X_OK),
            f"INVALID: {HOOK} is not executable",
        )
        # Prefix must not look like a test path: the scanner matches the path.
        self.tmp = Path(tempfile.mkdtemp(prefix="tgh-wrap-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = support.isolated_env(self.home)

    def _fire(self, tool: str, path: Path):
        payload = json.dumps(
            {"tool_name": tool, "tool_input": {"file_path": str(path)}}
        )
        return support.run_cmd(
            [str(HOOK)], self.tmp, self.env, stdin=payload, expect=None
        )

    def _rc(self, payload: str) -> int:
        return support.run_cmd(
            [str(HOOK)], self.tmp, self.env, stdin=payload, expect=None
        ).returncode

    def test_never_blocks_a_tool_call(self):
        self.assertGreater(len(JUNK), 0)
        for payload in JUNK:
            with self.subTest(payload=payload[:40]):
                self.assertEqual(
                    self._rc(payload),
                    0,
                    f"wrapper blocked on {payload!r}",
                )

    def test_catches_an_unguarded_plant_as_additional_context(self):
        path = self.tmp / "thing-test.sh"
        support.plant_text(path, UNGUARDED, recognisable="sed -i")
        self.assertIn("sed -i", path.read_text(encoding="utf-8"))
        proc = self._fire("Write", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "unguarded-plant",
            proc.stdout,
            f"did not catch an unguarded plant: {proc.stdout!r}",
        )
        self.assertTrue(
            support.has_additional_context(proc.stdout),
            f"finding was not additionalContext: {proc.stdout!r}",
        )
        json.loads(proc.stdout)
        self.assertIn(
            "thing-test.sh",
            proc.stdout,
            f"finding does not name the file: {proc.stdout!r}",
        )

    def test_catches_the_truncating_self_read_in_a_non_test_file(self):
        # Assemble the fault so scanning THIS file does not contain it.
        path = self.tmp / "helper.py"
        opener = 'open(p, "wb")'
        reader = 'open(p, "rb").read()'
        body = "import sys\np = sys.argv[1]\n" + opener + ".write(" + reader + ")\n"
        support.plant_text(path, body, recognisable="import sys")
        landed = path.read_text(encoding="utf-8")
        needle = opener + ".write(" + reader + ")"
        self.assertIn(
            needle,
            landed,
            "INVALID: fixture does not contain the truncating self-read",
        )
        self.assertNotEqual(landed, "", "INVALID: fixture was emptied")
        proc = self._fire("Edit", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            "truncating-self-read",
            proc.stdout,
            f"did not catch the truncating self-read: {proc.stdout!r}",
        )

    def test_stays_quiet_where_it_should(self):
        guarded = self.tmp / "ok-test.sh"
        support.plant_text(guarded, GUARDED, recognisable="cksum")
        self.assertIn("cksum", guarded.read_text(encoding="utf-8"))
        not_test = self.tmp / "bump-version.sh"
        support.plant_text(not_test, NOT_A_TEST, recognisable="pyproject")
        unguarded = self.tmp / "thing-test.sh"
        support.plant_text(unguarded, UNGUARDED, recognisable="sed -i")

        g = self._fire("Write", guarded)
        self.assertEqual(g.returncode, 0, g.stderr)
        self.assertFalse(
            support.has_additional_context(g.stdout),
            f"nagged a guarded test: {g.stdout!r}",
        )
        n = self._fire("Write", not_test)
        self.assertEqual(n.returncode, 0, n.stderr)
        self.assertFalse(
            support.has_additional_context(n.stdout),
            f"nagged a release script: {n.stdout!r}",
        )
        b = self._fire("Bash", unguarded)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertFalse(
            support.has_additional_context(b.stdout),
            f"nagged a non-Write/Edit tool: {b.stdout!r}",
        )


if __name__ == "__main__":
    unittest.main()
