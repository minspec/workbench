"""verify: run a command and report it in the envelope.

Written from SPEC.md, before the module existed. Each test names the
contract rule it pins:

  V1  a claim that holds is ok / approve, with no findings
  V2  a claim that fails is ok / changes, with one pasteable reproduce
  V3  a missing executable is invalid with a note — never changes
  V4  a non-existent cwd is invalid, not a crash
  V5  expect is matched against stdout AND stderr combined
  V6  expect_exit is the code that means the claim HELD
  V7  a timeout is tripped, and the process is not left running
  V8  a string command raises ValueError rather than being shell-split
  V9  spend is a zero-token run that DID run
  V10 stamp.ref is cwd's git HEAD, or the literal "worktree"
  V11 raw combined output is a file named by artifacts.raw, never inlined
  V12 main is a CLI: JSON on stdout; 0 / 1 / 2 / 64
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import support

verify = support.load("verify")


def _git_env(home: Path) -> dict:
    # GIT_DIR / GIT_WORK_TREE in the caller environment would aim git
    # at the snapshot (or its parent). Fixtures must be closed worlds,
    # and this suite must not run git against the snapshot's own repo.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "XDG_CONFIG_HOME"}
    env.update({
        "HOME": str(home),
        "GIT_AUTHOR_NAME": "verify-test",
        "GIT_AUTHOR_EMAIL": "verify-test@example.test",
        "GIT_COMMITTER_NAME": "verify-test",
        "GIT_COMMITTER_EMAIL": "verify-test@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _cmdline_pids(marker: str) -> list[int]:
    """Pids whose argv contains *marker*. Linux /proc; empty if absent."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    found = []
    me = os.getpid()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (OSError, FileNotFoundError):
            continue
        text = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        if marker in text:
            found.append(pid)
    return found


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    return Path(f"/proc/{pid}").exists()


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = verify.main(argv)
    return code, out.getvalue(), err.getvalue()


class _TempCwd(unittest.TestCase):
    """A throwaway cwd. Never the snapshot's own tree."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self._markers: list[str] = []

    def tearDown(self):
        for marker in self._markers:
            for pid in _cmdline_pids(marker):
                # Already gone is the outcome we wanted anyway.
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        self._td.cleanup()

    def parse_stdout(self, out):
        # An empty stdout is not an envelope. Failing here against the
        # stub (which prints nothing) is the honest red for CLI tests
        # whose exit code is 0, because that 0 is also the stub's return.
        self.assertTrue(out.strip(), "envelope JSON on stdout")
        try:
            env = json.loads(out)
        except json.JSONDecodeError:
            self.fail(f"stdout was not JSON: {out!r}")
        self.assertIsInstance(env, dict)
        return env

    def token(self, prefix):
        return f"{prefix}-{uuid.uuid4().hex}"


class AClaimThatHoldsApproves(_TempCwd):
    """V1 — the mapping's first row: it ran, it held, nothing to list."""

    def test_a_held_claim_is_ok_approve_with_no_findings(self):
        env = verify.check(
            "python exits 0",
            [sys.executable, "-c", "pass"],
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(env.get("findings"), [])
        self.assertEqual(env.get("job"), "verify")


class AClaimThatFailsReportsOneFinding(_TempCwd):
    """V2 — a failed claim is still a completed look; the finding is
    the command a human pastes to see the same result."""

    def test_a_wrong_exit_is_ok_changes_with_one_pasteable_finding(self):
        command = [sys.executable, "-c", "raise SystemExit(1)"]
        env = verify.check(
            "python exits 0",
            command,
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "changes")
        findings = env.get("findings") if isinstance(env.get("findings"), list) else []
        self.assertEqual(len(findings), 1)
        # shlex.join, not " ".join: the -c argument contains a space,
        # and an unquoted join is not pasteable.
        self.assertEqual(findings[0].get("reproduce"), shlex.join(command))


class AMissingExecutableIsInvalidNeverChanges(_TempCwd):
    """V3 — the reason this module exists. 'The claim is false' and
    'we could not evaluate the claim' are different answers, and
    returning changes for a missing binary reports a defect nobody
    has evidence for."""

    def test_a_missing_executable_is_invalid_with_a_note(self):
        missing = self.root / "no-such-dir" / "verify-no-such-exe-7e1c9a3d"
        env = verify.check(
            "the missing tool runs",
            [str(missing)],
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "invalid")
        # verdict None is the contract's null. Pinning only
        # `is not "changes"` would pass against the stub's {}.
        self.assertIsNone(env.get("verdict"))
        self.assertNotEqual(env.get("verdict"), "changes")
        self.assertEqual(env.get("findings"), [])
        note = env.get("note")
        self.assertIsInstance(note, str)
        self.assertTrue(note.strip(), "invalid must say why it could not run")

    def test_a_file_that_is_not_executable_is_invalid_not_changes(self):
        # Same mapping row: the process never starts. Invoking the
        # interpreter on the file would hide the defect this pins.
        script = self.cwd / "noexec.py"
        script.write_text(
            "#!/usr/bin/env python3\nprint('ran')\n", encoding="utf-8")
        script.chmod(0o644)
        env = verify.check(
            "the script runs",
            [str(script)],
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "invalid")
        self.assertIsNone(env.get("verdict"))
        self.assertNotEqual(env.get("verdict"), "changes")
        self.assertIsInstance(env.get("note"), str)
        self.assertTrue(env.get("note", "").strip())


class ANonexistentCwdIsInvalid(_TempCwd):
    """V4 — a bad cwd is the same class of refusal as a missing
    binary: we could not look, so we must not crash and must not
    invent a verdict."""

    def test_a_missing_cwd_is_invalid_not_a_crash(self):
        missing = self.root / "cwd-does-not-exist"
        try:
            env = verify.check(
                "python exits 0",
                [sys.executable, "-c", "pass"],
                cwd=str(missing),
            )
        except Exception as exc:
            self.fail(f"non-existent cwd must not crash: {exc}")
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "invalid")
        self.assertIsNone(env.get("verdict"))
        self.assertIsInstance(env.get("note"), str)
        self.assertTrue(env.get("note", "").strip())

    def test_a_cwd_that_is_a_file_is_invalid(self):
        not_a_dir = self.root / "not-a-dir"
        not_a_dir.write_text("x\n", encoding="utf-8")
        try:
            env = verify.check(
                "python exits 0",
                [sys.executable, "-c", "pass"],
                cwd=str(not_a_dir),
            )
        except Exception as exc:
            self.fail(f"a file as cwd must not crash: {exc}")
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "invalid")
        self.assertIsNone(env.get("verdict"))


class ExpectIsCheckedAgainstCombinedOutput(_TempCwd):
    """V5 — expect is a substring of stdout and stderr together.
    Checking only one stream would approve a claim whose evidence
    was on the other, or reject one whose evidence was on stderr."""

    def test_expect_on_stdout_approves(self):
        token = self.token("EXPECT_STDOUT")
        env = verify.check(
            "stdout carries the token",
            [sys.executable, "-c", f"print({token!r})"],
            cwd=str(self.cwd),
            expect=token,
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(env.get("findings"), [])

    def test_expect_on_stderr_only_approves(self):
        token = self.token("EXPECT_STDERR")
        env = verify.check(
            "stderr carries the token",
            [sys.executable, "-c",
             f"import sys; sys.stderr.write({token!r} + '\\n')"],
            cwd=str(self.cwd),
            expect=token,
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(env.get("findings"), [])

    def test_a_zero_exit_that_lacks_expect_is_changes(self):
        env = verify.check(
            "output contains a token that was never printed",
            [sys.executable, "-c", "print('hello')"],
            cwd=str(self.cwd),
            expect=self.token("EXPECT_ABSENT"),
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "changes")
        findings = env.get("findings") if isinstance(env.get("findings"), list) else []
        self.assertEqual(len(findings), 1)

    def test_expect_present_does_not_save_a_wrong_exit(self):
        token = self.token("EXPECT_AND_FAIL")
        env = verify.check(
            "exits 0 and prints the token",
            [sys.executable, "-c",
             f"print({token!r}); raise SystemExit(1)"],
            cwd=str(self.cwd),
            expect=token,
            expect_exit=0,
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "changes")


class ExpectExitNamesTheCodeThatMeansTheClaimHeld(_TempCwd):
    """V6 — expect_exit=1 means 'I assert this command FAILS'.
    Treating any nonzero as changes would make that assertion
    impossible; treating exit 0 as always-approve would make it a lie."""

    def test_exit_one_with_expect_exit_one_is_approve_not_changes(self):
        env = verify.check(
            "the command fails",
            [sys.executable, "-c", "raise SystemExit(1)"],
            cwd=str(self.cwd),
            expect_exit=1,
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "approve")
        self.assertNotEqual(env.get("verdict"), "changes")
        self.assertEqual(env.get("findings"), [])

    def test_exit_zero_with_expect_exit_one_is_changes(self):
        # The claim was that the command fails. It did not.
        env = verify.check(
            "the command fails",
            [sys.executable, "-c", "pass"],
            cwd=str(self.cwd),
            expect_exit=1,
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "changes")


class ATimeoutTripsAndDoesNotLeaveTheProcessRunning(_TempCwd):
    """V7 — a timeout is a trip, not a failed claim. The envelope
    cannot see a sleeper still burning a core; the process table can."""

    def test_a_timeout_is_tripped_and_the_process_is_gone(self):
        marker = self.token("VERIFY-V7")
        self._markers.append(marker)
        pidfile = self.root / "sleeper.pid"
        sleeper = self.root / "sleeper.py"
        sleeper.write_text(
            "import os, sys, time\n"
            "path, marker = sys.argv[1], sys.argv[2]\n"
            "with open(path, 'w') as f:\n"
            "    f.write(str(os.getpid()))\n"
            "    f.flush()\n"
            "    os.fsync(f.fileno())\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        env = verify.check(
            "the sleeper finishes",
            [sys.executable, str(sleeper), str(pidfile), marker],
            cwd=str(self.cwd),
            timeout=1,
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(env.get("status"), "tripped")
        self.assertIsNone(env.get("verdict"))
        # The command must have started: otherwise we are asserting
        # 'not running' about a process that was never launched, and
        # the stub would pass that half.
        self.assertTrue(
            pidfile.is_file(),
            "the command must have started before being timed out",
        )
        pid = int(pidfile.read_text(encoding="utf-8").strip())
        leftover = [p for p in _cmdline_pids(marker) if _pid_exists(p)]
        self.assertFalse(
            _pid_exists(pid),
            f"pid {pid} was left running after timeout",
        )
        self.assertEqual(
            leftover, [],
            f"process(es) still running with marker {marker}: {leftover}",
        )


class AStringCommandIsRefused(_TempCwd):
    """V8 — a shell string is how an argv becomes an injection.
    subprocess will accept a single-token string as argv[0]; the
    refusal has to happen before that."""

    def test_a_string_path_raises_value_error(self):
        # sys.executable as a string would run, if anyone passed it
        # through to subprocess. The type is the whole pin.
        with self.assertRaises(ValueError):
            verify.check(
                "python runs",
                sys.executable,
                cwd=str(self.cwd),
            )

    def test_a_shell_string_is_not_executed(self):
        target = self.root / "must-not-be-created"
        with self.assertRaises(ValueError):
            verify.check(
                "the file is touched",
                f"touch {target}",
                cwd=str(self.cwd),
            )
        self.assertFalse(target.exists())


class SpendRecordsAFreeRun(_TempCwd):
    """V9 — a verify costs no tokens and says so. runs is 1 because
    it DID run; the default 0 would tell worth.py that nothing happened."""

    def test_spend_is_zero_tokens_and_one_run(self):
        env = verify.check(
            "python exits 0",
            [sys.executable, "-c", "pass"],
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        self.assertEqual(
            env.get("spend"),
            {"harness": None, "total": 0, "out": 0, "runs": 1},
        )


class StampRefComesFromTheCwd(_TempCwd):
    """V10 — the envelope requires a ref. Without one a fact names
    no state, and cannot be re-checked. cwd is the state that was
    looked at; os.getcwd() is the test runner and is not that."""

    def test_a_non_repo_cwd_stamps_the_literal_worktree(self):
        env = verify.check(
            "python exits 0",
            [sys.executable, "-c", "pass"],
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        stamp = env.get("stamp") if isinstance(env.get("stamp"), dict) else {}
        self.assertEqual(stamp.get("ref"), "worktree")

    def test_a_repo_cwd_stamps_git_head(self):
        repo = self.root / "repo"
        repo.mkdir()
        env = _git_env(self.root)
        saved = {k: os.environ[k] for k in list(os.environ)
                 if k.startswith("GIT_")}
        for k in list(saved):
            del os.environ[k]
        try:
            def git(*args):
                r = subprocess.run(
                    ["git", *args], cwd=repo, env=env,
                    capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(
                        f"git {args} failed ({r.returncode}): {r.stderr}")
                return r

            git("init")
            git("config", "user.name", "verify-test")
            git("config", "user.email", "verify-test@example.test")
            git("config", "commit.gpgsign", "false")
            (repo / "README").write_text("fixture\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-m", "init")
            head = git("rev-parse", "HEAD").stdout.strip()
            self.assertTrue(head, "fixture HEAD must exist")

            result = verify.check(
                "python exits 0",
                [sys.executable, "-c", "pass"],
                cwd=str(repo),
            )
        finally:
            for k in list(os.environ):
                if k.startswith("GIT_"):
                    del os.environ[k]
            os.environ.update(saved)

        self.assertIsInstance(result, dict)
        stamp = result.get("stamp") if isinstance(result.get("stamp"), dict) else {}
        self.assertEqual(stamp.get("ref"), head)


class RawOutputLivesInAFileNotTheEnvelope(_TempCwd):
    """V11 — artifacts are handles, not contents. The property the
    envelope exists for is that iteration N costs the caller about
    what iteration 1 cost, and the way that breaks is prose migrating
    into the dict. Even a short output belongs in the file."""

    def test_combined_output_is_in_the_named_file_and_not_the_envelope(self):
        stdout_tok = self.token("RAW_STDOUT")
        stderr_tok = self.token("RAW_STDERR")
        before = {
            p.relative_to(self.cwd).as_posix(): p.read_bytes()
            for p in self.cwd.rglob("*") if p.is_file()
        }
        env = verify.check(
            "the command prints both tokens",
            [sys.executable, "-c",
             ("import sys\n"
              f"sys.stdout.write({stdout_tok!r})\n"
              f"sys.stderr.write({stderr_tok!r})\n")],
            cwd=str(self.cwd),
        )
        self.assertIsInstance(env, dict)
        artifacts = env.get("artifacts") if isinstance(env.get("artifacts"), dict) else {}
        raw = artifacts.get("raw")
        self.assertIsInstance(raw, str)
        self.assertTrue(raw.strip(), "artifacts.raw must name a file")
        raw_path = Path(raw)
        self.assertTrue(raw_path.is_file(), f"artifacts.raw is not a file: {raw!r}")
        body = raw_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn(stdout_tok, body)
        self.assertIn(stderr_tok, body)
        # The handle may appear in the envelope; the output must not.
        blob = json.dumps(env)
        self.assertNotIn(stdout_tok, blob)
        self.assertNotIn(stderr_tok, blob)
        after = {
            p.relative_to(self.cwd).as_posix(): p.read_bytes()
            for p in self.cwd.rglob("*") if p.is_file()
        }
        # verify runs commands; it does not edit cwd. The raw file
        # therefore cannot live inside the tree it is judging.
        self.assertEqual(after, before)


class MainIsACli(_TempCwd):
    """V12 — stdout is the envelope; the exit code is the verdict
    routed for a caller that does not parse JSON. 64 is usage, not
    argparse's 2, because 2 is already taken by invalid/tripped."""

    def test_an_approving_run_prints_json_and_returns_zero(self):
        code, out, _err = run_main(
            ["--claim", "python exits 0", "--cwd", str(self.cwd),
             "--", sys.executable, "-c", "pass"],
        )
        env = self.parse_stdout(out)
        self.assertEqual(env.get("job"), "verify")
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(code, 0)

    def test_a_failing_run_prints_json_and_returns_one(self):
        code, out, _err = run_main(
            ["--claim", "python exits 0", "--cwd", str(self.cwd),
             "--", sys.executable, "-c", "raise SystemExit(1)"],
        )
        self.assertEqual(code, 1)
        env = self.parse_stdout(out)
        self.assertEqual(env.get("status"), "ok")
        self.assertEqual(env.get("verdict"), "changes")

    def test_an_invalid_run_prints_json_and_returns_two(self):
        missing = self.root / "no-such-dir" / "verify-no-such-exe"
        code, out, _err = run_main(
            ["--claim", "the tool runs", "--cwd", str(self.cwd),
             "--", str(missing)],
        )
        self.assertEqual(code, 2)
        env = self.parse_stdout(out)
        self.assertEqual(env.get("status"), "invalid")
        self.assertIsNone(env.get("verdict"))

    def test_a_tripped_run_prints_json_and_returns_two(self):
        marker = self.token("VERIFY-V12-TRIP")
        self._markers.append(marker)
        sleeper = self.root / "cli-sleeper.py"
        sleeper.write_text(
            "import sys, time\n"
            "marker = sys.argv[1]\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        code, out, _err = run_main(
            ["--claim", "the sleeper finishes", "--cwd", str(self.cwd),
             "--timeout", "1",
             "--", sys.executable, str(sleeper), marker],
        )
        self.assertEqual(code, 2)
        env = self.parse_stdout(out)
        self.assertEqual(env.get("status"), "tripped")
        self.assertIsNone(env.get("verdict"))
        leftover = [p for p in _cmdline_pids(marker) if _pid_exists(p)]
        self.assertEqual(
            leftover, [],
            f"CLI timeout left process(es) running: {leftover}",
        )

    def test_expect_exit_on_the_cli_approves_a_failure(self):
        code, out, _err = run_main(
            ["--claim", "the command fails", "--cwd", str(self.cwd),
             "--expect-exit", "1",
             "--", sys.executable, "-c", "raise SystemExit(1)"],
        )
        env = self.parse_stdout(out)
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(code, 0)

    def test_expect_on_the_cli_is_honoured(self):
        token = self.token("CLI_EXPECT")
        code, out, _err = run_main(
            ["--claim", "stdout carries the token", "--cwd", str(self.cwd),
             "--expect", token,
             "--", sys.executable, "-c", f"print({token!r})"],
        )
        env = self.parse_stdout(out)
        self.assertEqual(env.get("verdict"), "approve")
        self.assertEqual(code, 0)

    def test_a_usage_error_returns_sixty_four(self):
        for argv in (
            [],
            ["--claim", "x"],
            ["--cwd", str(self.cwd)],
            ["--claim", "x", "--cwd", str(self.cwd)],
            ["--bogus"],
        ):
            with self.subTest(argv=argv):
                code, _out, _err = run_main(argv)
                self.assertEqual(code, 64)


if __name__ == "__main__":
    unittest.main()
