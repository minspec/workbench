#!/usr/bin/env python3
"""Keep the conductor on the dispatch levers.

Reads Claude Code's PreToolUse JSON payload on stdin.  A refusal is the same
``hookSpecificOutput`` decision used by the precheck next door; an allow is
silent.  POLICY is deliberately a table: additions should be reviewable as
policy changes, not hidden in control flow.
"""

import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from command_shape import commands, statements

AGENT_REASON = (
    "Own sub-agents are retired. Delegate through the levers: "
    "grok-dispatch.sh (investigation), fable-dispatch.sh (Claude-specific / "
    "high-judgment), codex-dispatch.sh (implementation), apply-push.sh (landing)."
)
INVESTIGATION_REASON = (
    "The conductor may not investigate or edit repository contents by hand. "
    "Delegate investigation through grok-dispatch.sh."
)

# Ordered, auditable policy.  Explicit allows win before any deny is tested.
POLICY = {
    "allow_anywhere": (
        "grok-dispatch.sh",
        "fable-dispatch.sh",
        "codex-dispatch.sh",
        "apply-push.sh",
    ),
    "allow_git": ("fetch", "rev-parse", "ls-remote", "push", "status"),
    "deny_git": (
        "diff", "log", "show", "interpret-trailers", "apply", "merge",
        "rebase", "cherry-pick", "blame",
    ),
    "deny_gh": (
        r"^gh\s+pr\s+diff\b",
        r"^gh\s+pr\s+view\b(?=.*(?:^|\s)--json(?:\s|=))",
        r"^gh\s+run\s+view\b",
        r"^gh\s+api\b",
    ),
    "forensic_programs": ("sed", "awk", "gawk", "mawk", "grep", "egrep", "fgrep"),
    "job_readers": ("cat", "python", "python3", "ls"),
    "job_markers": ("/jobs/", "/scratchpad/", ".dev/jobs/", ".dev/scratchpad/"),
}


def _git_subcommand(command):
    match = re.match(r"^git\s+([^\s]+)", command)
    return match.group(1) if match else None


def _is_job_read(command):
    words = command.split()
    if not words or words[0].rsplit("/", 1)[-1] not in POLICY["job_readers"]:
        return False
    padded = "/" + command.lstrip("./")
    return any(marker in padded for marker in POLICY["job_markers"])


def _tracked_files():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],  # noqa: S607
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    return tuple(
        path.decode("utf-8", "surrogateescape")
        for path in result.stdout.split(b"\0")
        if path
    )


def _forensic_over_tracked(statement, tracked):
    if not any(
        re.search(rf"(?:^|[|;&]\s*){program}\b", statement)
        for program in POLICY["forensic_programs"]
    ):
        return False
    try:
        words = shlex.split(statement, comments=True)
    except ValueError:
        words = statement.split()
    for word in words:
        candidate = word.removeprefix("./")
        if candidate in tracked or any(
            path.startswith(candidate.rstrip("/") + "/")
            for path in tracked
            if candidate
        ):
            return True
    return False


def deny_reason(payload):
    """Return a reason to deny, or None to allow."""
    tool = payload.get("tool_name")
    if tool == "Agent":
        return AGENT_REASON
    if tool != "Bash":
        return None

    command = ((payload.get("tool_input") or {}).get("command") or "")[:20000]
    if any(lever in command for lever in POLICY["allow_anywhere"]):
        return None

    variants = commands(command)
    for variant in variants:
        subcommand = _git_subcommand(variant)
        if subcommand in POLICY["allow_git"]:
            continue
        if subcommand in POLICY["deny_git"]:
            return INVESTIGATION_REASON
        if any(re.search(pattern, variant) for pattern in POLICY["deny_gh"]):
            return INVESTIGATION_REASON

    tracked = _tracked_files()
    for statement in statements(command):
        if _is_job_read(statement):
            continue
        if _forensic_over_tracked(statement, tracked):
            return INVESTIGATION_REASON
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    reason = deny_reason(payload)
    if reason:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
