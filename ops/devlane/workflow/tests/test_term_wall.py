import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


CHECK = Path(__file__).resolve().parents[1] / "checks" / "term_wall.py"
PATTERN = "zz[q]orblat"
PLANT = "zzqorblat"
MASK = "[forbidden name]"


class TermWallTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self._git("init", "-q")
        self._git("config", "user.name", "Term Wall Test")
        self._git("config", "user.email", "term-wall-test@example.invalid")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._environment(PATTERN),
        )

    def _environment(self, pattern=PATTERN):
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.root),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        if pattern is not None:
            environment["TERM_WALL"] = pattern
        return environment

    def _run(self, *arguments, pattern=PATTERN, input_text=None, cwd=None):
        return subprocess.run(
            [sys.executable, str(CHECK), *map(str, arguments)],
            cwd=str(cwd or self.root),
            env=self._environment(pattern),
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _track(self, relative_path, content, binary=False):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        self._git("add", "--", relative_path)
        return path

    def _commit(self, message):
        self._git("commit", "-q", "--allow-empty", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def assertClean(self, result):
        self.assertEqual(0, result.returncode, result)
        self.assertEqual("", result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(1, len(lines), result.stdout)
        self.assertTrue(lines[0].strip())

    def assertHit(self, result, surface):
        self.assertEqual(1, result.returncode, result)
        self.assertEqual("", result.stderr)
        lines = result.stdout.splitlines()
        self.assertGreaterEqual(len(lines), 1)
        for line in lines:
            self.assertRegex(line, rf"^{re.escape(surface)}: .+: .+$")
            self.assertIn(MASK, line)
        self.assertMasked(result)

    def assertRefusal(self, result, refusal_class):
        self.assertEqual(2, result.returncode, result)
        self.assertEqual("", result.stdout)
        lines = result.stderr.splitlines()
        self.assertEqual(1, len(lines), result.stderr)
        self.assertRegex(
            lines[0],
            rf"^{re.escape(refusal_class)}: expected .+; found .+; needed .+$",
        )

    def assertMasked(self, result):
        combined = result.stdout + result.stderr
        self.assertNotIn(PLANT, combined)
        self.assertNotIn(PLANT.upper(), combined)

    def test_clean_tree(self):
        self._track("notes.txt", "ordinary text\n")
        self.assertClean(self._run("--root", self.root))

    def test_planted_tracked_content(self):
        self._track("notes.txt", f"before {PLANT} after\n")
        self.assertHit(self._run("--root", self.root), "content")

    def test_planted_tracked_path(self):
        self._track(f"docs/{PLANT}.txt", "ordinary text\n")
        self.assertHit(self._run("--root", self.root), "path")

    def test_message_file_clean(self):
        message = self.root / "message.txt"
        message.write_text("An ordinary commit message\n", encoding="utf-8")
        self.assertClean(self._run("--message-file", message))

    def test_message_file_planted(self):
        message = self.root / "message.txt"
        message.write_text(f"Mention {PLANT.upper()} here\n", encoding="utf-8")
        self.assertHit(self._run("--message-file", message), "message")

    def test_range_clean(self):
        base = self._commit("base message")
        head = self._commit("ordinary follow-up")
        self.assertClean(self._run("--root", self.root, "--range", f"{base}..{head}"))

    def test_range_planted(self):
        base = self._commit("base message")
        head = self._commit(f"follow-up mentioning {PLANT}")
        self.assertHit(
            self._run("--root", self.root, "--range", f"{base}..{head}"),
            "commit",
        )

    def test_stdin_clean_and_planted(self):
        self.assertClean(self._run("--stdin", input_text="ordinary input\n"))
        self.assertHit(
            self._run("--stdin", input_text=f"input with {PLANT}\n"),
            "stdin",
        )

    def test_missing_message_file_refuses(self):
        result = self._run("--message-file", self.root / "does-not-exist")
        self.assertRefusal(result, "message")

    def test_unresolvable_range_refuses(self):
        result = self._run("--root", self.root, "--range", "missing..also-missing")
        self.assertRefusal(result, "range")

    def test_not_a_git_work_tree_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run("--root", directory, cwd=directory)
        self.assertRefusal(result, "root")

    def test_unset_and_empty_pattern_refuse(self):
        for pattern in (None, ""):
            with self.subTest(pattern=pattern):
                result = self._run("--root", self.root, pattern=pattern)
                self.assertRefusal(result, "pattern")
                self.assertEqual(
                    "pattern: expected TERM_WALL set; found empty; needed the org variable "
                    "(CI) or ops/bin/term-wall.conf (local)\n",
                    result.stderr,
                )

    def test_pattern_falls_back_to_conf_file(self):
        config = self.root / "ops" / "bin" / "term-wall.conf"
        config.parent.mkdir(parents=True)
        config.write_text(PATTERN + "\n", encoding="utf-8")
        self._track("notes.txt", f"configured match: {PLANT}\n")
        self.assertHit(
            self._run("--root", self.root, pattern=None),
            "content",
        )

    def test_every_match_is_masked(self):
        self._track("notes.txt", f"{PLANT} and {PLANT.upper()}\n")
        result = self._run("--root", self.root)
        self.assertHit(result, "content")
        self.assertEqual(2, result.stdout.count(MASK))

    def test_binary_files_are_skipped(self):
        self._track("payload.bin", b"header\x00" + PLANT.encode("ascii") + b"\n", binary=True)
        self._track("image.png", PLANT.encode("ascii") + b"\n", binary=True)
        self.assertClean(self._run("--root", self.root))


if __name__ == "__main__":
    unittest.main()
