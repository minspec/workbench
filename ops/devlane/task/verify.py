#!/usr/bin/env python3
"""Verify a human claim without turning execution failure into evidence.

The distinction here is the reason this job exists: a command that
disproves a claim returns ``changes``, while a command that never ran returns
``invalid``. Raw output is kept out of the envelope because envelopes are
small routing records; the artifact they name is the evidence.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import envelope
import fileset

_SPEND = {"harness": None, "total": 0, "out": 0, "runs": 1}


class _UsageError(Exception):
    """An argparse refusal that ``main`` translates to exit 64."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        raise _UsageError(f"{self.prog}: error: {message}")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ref(cwd):
    """Name committed state when possible, without making Git a prerequisite."""
    try:
        return fileset._commit(cwd, "HEAD")
    except (fileset.FilesetError, TypeError, ValueError, OSError):
        # Verification is useful outside a repository too. A Git lookup
        # failure changes provenance, not whether the requested command ran.
        return "worktree"


def _raw_artifact():
    descriptor, path = tempfile.mkstemp(prefix="verify-", suffix=".raw")
    os.close(descriptor)
    return path


def _command_text(command):
    """Return a pasteable reproduction, or explain why argv is unusable."""
    try:
        return shlex.join([os.fsdecode(os.fspath(part)) for part in command])
    except (TypeError, ValueError) as exc:
        raise ValueError("command entries must be strings or path-like values") from exc


def _stop(process):
    """Stop the command and its ordinary descendants after a timeout."""
    if os.name == "posix":
        try:
            # The child starts a new session specifically so a timed-out
            # verifier cannot leave helpers running after their parent dies.
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    # A process that exited between the timeout and this kill is the
    # state we were trying to reach.
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def check(claim, command, *, cwd, expect=None, expect_exit=0,
          timeout=300) -> dict:
    """Run one argv command and return its mechanical answer as an envelope."""
    if not isinstance(claim, str) or not claim.strip() or "\n" in claim or "\r" in claim:
        raise ValueError("claim must be a non-empty one-line string")
    if not isinstance(command, list):
        # Shell-looking text is refused instead of guessed at or split. That
        # keeps caller-controlled punctuation from becoming shell syntax.
        raise ValueError("command must be an argv list, never a shell string")
    if expect is not None and not isinstance(expect, str):
        raise ValueError("expect must be a string or None")
    if not isinstance(expect_exit, int) or isinstance(expect_exit, bool):
        raise ValueError("expect_exit must be an integer")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")

    started = _now()
    ref = _ref(cwd)
    raw_path = _raw_artifact()
    artifacts = {"raw": raw_path}
    stamp = {"ref": ref, "started": started, "ended": None}

    try:
        reproduce = _command_text(command)
    except ValueError as exc:
        stamp["ended"] = _now()
        return envelope.build(
            "verify", status="invalid", verdict=None, artifacts=artifacts,
            spend=_SPEND, stamp=stamp,
            note=f"command could not run: {exc}",
        )
    if not command:
        stamp["ended"] = _now()
        return envelope.build(
            "verify", status="invalid", verdict=None, artifacts=artifacts,
            spend=_SPEND, stamp=stamp,
            note="command could not run: the argv list is empty",
        )

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=(os.name == "posix"),
        )
    except (OSError, TypeError, ValueError) as exc:
        stamp["ended"] = _now()
        return envelope.build(
            "verify", status="invalid", verdict=None, artifacts=artifacts,
            spend=_SPEND, stamp=stamp,
            note=f"command could not run: {exc}",
        )

    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop(process)
        # communicate() after the kill both reaps the process and drains the
        # output pipe, so neither a zombie nor evidence is left behind.
        output, _ = process.communicate()

    with open(raw_path, "wb") as raw_stream:
        raw_stream.write(output or b"")
    stamp["ended"] = _now()

    if timed_out:
        return envelope.build(
            "verify", status="tripped", verdict=None, artifacts=artifacts,
            spend=_SPEND, stamp=stamp,
            note=f"command exceeded timeout of {timeout} seconds",
        )

    combined = (output or b"").decode("utf-8", "replace")
    held = process.returncode == expect_exit
    if expect is not None:
        held = held and expect in combined

    if held:
        return envelope.build(
            "verify", status="ok", verdict="approve", artifacts=artifacts,
            spend=_SPEND, stamp=stamp,
        )

    finding = envelope.finding(
        "p2", str(cwd), claim, reproduce=reproduce,
    )
    return envelope.build(
        "verify", status="ok", verdict="changes", findings=[finding],
        artifacts=artifacts, spend=_SPEND, stamp=stamp,
    )


def _parser():
    parser = _Parser(prog="verify.py")
    parser.add_argument("--claim", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--expect")
    parser.add_argument("--expect-exit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv=None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command[:1] != ["--"]:
            raise _UsageError(
                "verify.py: error: -- must separate options from command")
        # REMAINDER deliberately preserves the separator. It marks the
        # boundary for argparse and is not part of the requested argv.
        arguments.command = arguments.command[1:]
        if not arguments.command:
            raise _UsageError("verify.py: error: command after -- is required")
    except _UsageError as exc:
        print(exc, file=sys.stderr)
        return 64
    except SystemExit as exc:
        # argparse owns --help output, but main remains callable as a function.
        return int(exc.code)

    try:
        result = check(
            arguments.claim,
            arguments.command,
            cwd=arguments.cwd,
            expect=arguments.expect,
            expect_exit=arguments.expect_exit,
            timeout=arguments.timeout,
        )
    except ValueError as exc:
        print(f"verify.py: error: {exc}", file=sys.stderr)
        return 64

    print(json.dumps(result))
    if result["verdict"] == "approve":
        return 0
    if result["verdict"] == "changes":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
