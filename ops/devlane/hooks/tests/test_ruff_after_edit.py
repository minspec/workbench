"""Moved ruff-after-edit.sh corpus, plus D4 as a stub ruff on PATH.

The in-file --test staged files inside the real worktree and needed a
runnable ruff. These cases use a throwaway git repo and a stub whose
answers are proved before the hook is asked anything.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import support

HOOK = support.CLAUDE_DIR / "ruff-after-edit.sh"

# A stub that answers per-file, matching the moved corpus's three plants:
# bad.py is a lint finding, good.py is clean, fmt.py is format-dirty / lint-clean.
CORPUS_STUB = """#!/bin/sh
set -eu
if [ "${1:-}" = "--version" ]; then
    echo "ruff 0.0.0-stub"
    exit 0
fi
cmd="${1:-}"
shift || true
file=""
for a in "$@"; do
    case "$a" in
        --quiet|--check) ;;
        *) file="$a" ;;
    esac
done
base=$(basename "$file")
case "$cmd" in
    format)
        if [ "$base" = "fmt.py" ]; then exit 1; fi
        exit 0
        ;;
    check)
        if [ "$base" = "bad.py" ]; then
            printf '%s\\n' "${file}:1:1: F401 unused import"
            exit 1
        fi
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
"""

# D4: --version ok, format --check rc 2, check rc 0 empty.
D4_FORMAT_RC2_STUB = """#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "ruff 0.0.0-stub"; exit 0; fi
if [ "${1:-}" = "format" ]; then exit 2; fi
if [ "${1:-}" = "check" ]; then exit 0; fi
exit 0
"""

# D4 if the format branch is gone: check rc 2 and empty stdout → silence.
D4_CHECK_RC2_STUB = """#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "ruff 0.0.0-stub"; exit 0; fi
if [ "${1:-}" = "format" ]; then exit 0; fi
if [ "${1:-}" = "check" ]; then exit 2; fi
exit 0
"""


class RuffAfterEdit(unittest.TestCase):
    def setUp(self):
        support.require_jq()
        self.assertTrue(HOOK.is_file(), f"INVALID: hook missing: {HOOK}")
        self.tmp = Path(tempfile.mkdtemp(prefix="ruff-hook-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.repo = self.tmp / "repo"

    def _env(self):
        return support.isolated_env(self.home, extra_path=self.bin)

    def _git_repo(self, *, ruff_toml: str | None = ""):
        self.repo.mkdir()
        env = self._env()
        support.git(self.repo, env, "-c", "init.defaultBranch=main", "init", "-q")
        support.configure_identity(self.repo, env)
        if ruff_toml is not None:
            support.plant_text(
                self.repo / "ruff.toml", ruff_toml,
                recognisable=ruff_toml or None,
            )
            if ruff_toml == "":
                # empty file is still a config file; prove it exists and is empty
                landed = (self.repo / "ruff.toml").read_text(encoding="utf-8")
                self.assertEqual(landed, "", "INVALID: ruff.toml plant drifted")
        return env

    def _stub(self, body: str):
        stub = support.plant_executable(self.bin / "ruff", body)
        which = shutil.which("ruff", path=str(self.bin) + os.pathsep + os.environ.get("PATH", ""))
        self.assertEqual(
            Path(which).resolve(), stub.resolve(),
            f"INVALID: which ruff is {which}, not the stub {stub}",
        )
        return stub

    def _prove_stub(self, env, *, version_rc=0, check_rc=None, check_out=None,
                    format_rc=None, file="x.py"):
        v = support.run_cmd(["ruff", "--version"], self.repo, env, expect=version_rc)
        self.assertIn(
            "ruff", v.stdout.lower(),
            f"INVALID: stub --version did not identify itself: {v.stdout!r}",
        )
        if format_rc is not None:
            f = support.run_cmd(
                ["ruff", "format", "--check", file], self.repo, env, expect=None
            )
            self.assertEqual(
                f.returncode, format_rc,
                f"INVALID: stub format --check rc {f.returncode}, wanted {format_rc}",
            )
        if check_rc is not None:
            c = support.run_cmd(
                ["ruff", "check", "--quiet", file], self.repo, env, expect=None
            )
            self.assertEqual(
                c.returncode, check_rc,
                f"INVALID: stub check rc {c.returncode}, wanted {check_rc}",
            )
            if check_out is not None:
                self.assertEqual(
                    c.stdout, check_out,
                    f"INVALID: stub check stdout {c.stdout!r}, wanted {check_out!r}",
                )

    def _fire(self, env, payload: str):
        return support.run_script(HOOK, payload, self.repo, env)

    def _plant_py(self, name: str, body: str, *, recognisable: str):
        path = self.repo / name
        support.plant_text(path, body, recognisable=recognisable)
        self.assertTrue(path.is_file(), f"INVALID: {name} missing")
        self.assertTrue(str(path).endswith(".py"), f"INVALID: {name} not .py")
        return path

    def test_catches_a_lint_finding(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        path = self._plant_py("bad.py", "import os\n", recognisable="import os")
        self._prove_stub(
            env, check_rc=1,
            check_out=f"{path}:1:1: F401 unused import\n",
            file=str(path),
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertTrue(
            support.has_additional_context(proc.stdout),
            f"wanted a lint complaint, got {proc.stdout!r} / {proc.stderr!r}",
        )

    def test_stays_quiet_on_a_clean_py(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        path = self._plant_py("good.py", "x = 1\n", recognisable="x = 1")
        self._prove_stub(env, check_rc=0, check_out="", file=str(path))
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged a clean file: {proc.stdout!r}",
        )

    def test_stays_quiet_on_format_only(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        path = self._plant_py(
            "fmt.py", 'x = {  "a":1 }\n', recognisable='"a":1'
        )
        self._prove_stub(
            env, format_rc=1, check_rc=0, check_out="", file=str(path)
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"format-only was treated as a failure: {proc.stdout!r}",
        )

    def test_reads_serena_relative_path(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        path = self._plant_py("bad.py", "import os\n", recognisable="import os")
        self._prove_stub(
            env, check_rc=1,
            check_out=f"{path}:1:1: F401 unused import\n",
            file=str(path),
        )
        proc = self._fire(
            env,
            support.write_payload(
                relative_path="bad.py", cwd=str(self.repo)
            ),
        )
        self.assertTrue(
            support.has_additional_context(proc.stdout),
            f"relative_path was not read: {proc.stdout!r}",
        )
        self.assertTrue(path.is_file(), "INVALID: relative_path target vanished")

    def test_ignores_a_non_py_file(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        path = self.repo / "pyproject.toml"
        support.plant_text(path, "[project]\nname='x'\n", recognisable="name=")
        self.assertFalse(str(path).endswith(".py"))
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged a non-.py file: {proc.stdout!r}",
        )

    def test_ignores_a_file_that_is_not_there(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        gone = self.repo / "gone.py"
        self.assertFalse(gone.exists(), "INVALID: gone.py exists")
        proc = self._fire(env, support.write_payload(file_path=str(gone)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged a missing file: {proc.stdout!r}",
        )

    def test_ignores_a_payload_with_no_path(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        proc = self._fire(env, json.dumps({"tool_input": {}}))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged an empty payload: {proc.stdout!r}",
        )

    def test_ignores_junk_input(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        proc = self._fire(env, "not json at all")
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged junk input: {proc.stdout!r}",
        )

    def test_ignores_empty_input(self):
        env = self._git_repo()
        self._stub(CORPUS_STUB)
        proc = self._fire(env, "")
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged empty input: {proc.stdout!r}",
        )

    def test_noop_where_ruff_is_not_configured(self):
        env = self._git_repo(ruff_toml=None)
        self._stub(CORPUS_STUB)
        path = self._plant_py(
            "bad.py", 'x = {  "a":1 }\n', recognisable='"a":1'
        )
        self.assertFalse(
            (self.repo / "ruff.toml").exists(),
            "INVALID: ruff.toml present in the unconfigured case",
        )
        self._prove_stub(
            env, check_rc=1,
            check_out=f"{path}:1:1: F401 unused import\n",
            file=str(path),
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged an unconfigured repo: {proc.stdout!r}",
        )

    def test_d4_format_check_rc_2_is_not_a_formatting_violation(self):
        """Stub: --version ok, format --check rc 2, check rc 0 → no output."""
        env = self._git_repo()
        self._stub(D4_FORMAT_RC2_STUB)
        path = self._plant_py("x.py", "x = 1\n", recognisable="x = 1")
        self._prove_stub(
            env, format_rc=2, check_rc=0, check_out="", file=str(path)
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"D4: format rc 2 was reported as a formatting violation: "
            f"{proc.stdout!r}",
        )
        self.assertNotIn(
            "formatting", proc.stdout.lower(),
            f"D4: output mentioned formatting: {proc.stdout!r}",
        )

    def test_d4_check_rc_2_with_empty_stdout_is_silence(self):
        """If the format branch is gone: check rc 2 + empty stdout → silence."""
        env = self._git_repo()
        self._stub(D4_CHECK_RC2_STUB)
        path = self._plant_py("x.py", "x = 1\n", recognisable="x = 1")
        self._prove_stub(
            env, check_rc=2, check_out="", file=str(path)
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"D4: check rc 2 empty stdout was not silence: {proc.stdout!r}",
        )
