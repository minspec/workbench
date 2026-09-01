"""Load the hyphenated hook scripts and build throwaway git / PATH fixtures.

The real repository is never the fixture: every git directory these helpers
create lives under the caller's tempdir, and every stub binary is written
there too. A plant that did not land raises ``PlantFailed`` — that is
INVALID, not a verdict on the hook.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
HOOKS_DIR = TESTS_DIR.parent
CLAUDE_DIR = HOOKS_DIR / "claude"
WORKTREE_ROOT = TESTS_DIR.parents[3]

FIXED_DATE = "2026-01-02T03:04:05Z"


class PlantFailed(AssertionError):
    """The fixture was not what the case claimed to set up."""


def load_claude(filename: str, modname: str):
    """Load a script from ``claude/`` as a module without running ``__main__``."""
    path = CLAUDE_DIR / filename
    if not path.is_file():
        raise PlantFailed(
            f"INVALID: hook script does not exist yet: {path}"
        )
    if modname in sys.modules:
        return sys.modules[modname]
    # The hyphenated scripts import ``command_shape`` from this directory.
    claude = str(CLAUDE_DIR)
    if claude not in sys.path:
        sys.path.insert(0, claude)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise PlantFailed(f"INVALID: cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def isolated_env(home: Path, extra_path=None, extra=None):
    """Env that cannot see the real repo's git config or hook escape hatches."""
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if extra_path is not None:
        path = str(extra_path) + os.pathsep + path
    env = {
        "PATH": path,
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_DATE": FIXED_DATE,
        "GIT_COMMITTER_DATE": FIXED_DATE,
        "LC_ALL": "C",
        "LANG": "C",
    }
    # Never inherit the offline escape: a D2 case with this set is INVALID.
    env.pop("CLAUDE_PRECHECK_NO_FETCH", None)
    if extra:
        env.update(extra)
    return env


def run_cmd(argv, cwd, env, *, expect=None, stdin=None):
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if expect is not None and proc.returncode != expect:
        raise PlantFailed(
            f"INVALID: {argv!r} in {cwd} exited {proc.returncode} "
            f"(wanted {expect})\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


def git(cwd, env, *args, expect=0):
    return run_cmd(["git", *args], cwd, env, expect=expect)


def configure_identity(repo: Path, env):
    git(repo, env, "config", "user.name", "Test User")
    git(repo, env, "config", "user.email", "test@example.invalid")


def plant_text(path: Path, content: str, *, recognisable: str | None = None) -> str:
    """Write ``content`` and prove it landed intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    landed = path.read_text(encoding="utf-8")
    if landed != content:
        raise PlantFailed(
            f"INVALID: plant did not reach disk for {path} "
            f"(wanted {len(content)} bytes, got {len(landed)})"
        )
    if content and not landed:
        raise PlantFailed(f"INVALID: plant emptied {path}")
    if recognisable is not None and recognisable not in landed:
        raise PlantFailed(
            f"INVALID: plant left {path} unrecognisable as the fixture"
        )
    return landed


def plant_executable(path: Path, content: str) -> Path:
    """Write a stub binary, prove it is executable, prove it is the file we wrote."""
    plant_text(path, content, recognisable=content.splitlines()[0][:20])
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    mode = path.stat().st_mode
    if not (mode & stat.S_IXUSR):
        raise PlantFailed(f"INVALID: {path} is not executable")
    if not path.is_file():
        raise PlantFailed(f"INVALID: {path} is not a file")
    return path


def bash_payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def write_payload(file_path=None, relative_path=None, cwd=None) -> str:
    tool_input = {}
    if file_path is not None:
        tool_input["file_path"] = file_path
    if relative_path is not None:
        tool_input["relative_path"] = relative_path
    payload = {"tool_name": "Edit", "tool_input": tool_input}
    if cwd is not None:
        payload["cwd"] = cwd
    return json.dumps(payload)


def run_script(script: Path, payload: str, cwd: Path, env) -> subprocess.CompletedProcess:
    if not script.is_file():
        raise PlantFailed(f"INVALID: script missing: {script}")
    argv = (["python3", str(script)] if script.suffix == ".py"
            else ["bash", str(script)])
    return run_cmd(argv, cwd, env, stdin=payload, expect=None)


def has_additional_context(stdout: str) -> bool:
    if not stdout or not stdout.strip():
        return False
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return "additionalContext" in stdout
    hook = data.get("hookSpecificOutput") or {}
    return bool(hook.get("additionalContext"))


def permission_decision(stdout: str):
    if not stdout or not stdout.strip():
        return None, ""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None, stdout
    hook = data.get("hookSpecificOutput") or {}
    return hook.get("permissionDecision"), hook.get("permissionDecisionReason") or ""


class StaleClone:
    """Bare remote + clone whose FETCH_HEAD is gone and whose origin is behind."""

    def __init__(self, tmp: Path, env):
        self.tmp = tmp
        self.env = env
        self.seed = tmp / "seed"
        self.bare = tmp / "remote.git"
        self.clone = tmp / "clone"
        self.old_sha = None
        self.new_sha = None

    def build(self):
        self.seed.mkdir()
        git(self.seed, self.env, "-c", "init.defaultBranch=main", "init", "-q")
        configure_identity(self.seed, self.env)
        plant_text(self.seed / "file.txt", "one\n", recognisable="one")
        git(self.seed, self.env, "add", "file.txt")
        git(self.seed, self.env, "commit", "-q", "-m", "A", "--date", FIXED_DATE)
        self.old_sha = git(
            self.seed, self.env, "rev-parse", "HEAD"
        ).stdout.strip()
        if len(self.old_sha) < 7:
            raise PlantFailed("INVALID: seed commit SHA missing")

        git(self.tmp, self.env, "clone", "--bare", "-q",
            str(self.seed), str(self.bare))
        git(self.tmp, self.env, "clone", "-q", str(self.bare), str(self.clone))
        configure_identity(self.clone, self.env)

        fetch_head = self.clone / ".git" / "FETCH_HEAD"
        # git 2.43's clone from a local path does not write FETCH_HEAD.
        # That is already D2's missing-FETCH_HEAD state. Older gits do
        # write one; delete it so both land on the same fixture.
        if fetch_head.is_file():
            before = fetch_head.read_bytes()
            if not before:
                raise PlantFailed("INVALID: FETCH_HEAD was already empty")
            fetch_head.unlink()
        if fetch_head.exists():
            raise PlantFailed("INVALID: FETCH_HEAD is still present")

        origin_main = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if origin_main != self.old_sha:
            raise PlantFailed(
                f"INVALID: clone origin/main is {origin_main}, not seed {self.old_sha}"
            )

        plant_text(self.seed / "file.txt", "two\n", recognisable="two")
        landed = (self.seed / "file.txt").read_text(encoding="utf-8")
        if landed == "one\n":
            raise PlantFailed("INVALID: seed file was not advanced")
        git(self.seed, self.env, "add", "file.txt")
        git(self.seed, self.env, "commit", "-q", "-m", "B", "--date", FIXED_DATE)
        self.new_sha = git(
            self.seed, self.env, "rev-parse", "HEAD"
        ).stdout.strip()
        if self.new_sha == self.old_sha:
            raise PlantFailed("INVALID: seed HEAD did not move")
        git(self.seed, self.env, "push", "-q", str(self.bare), "main")
        bare_head = git(
            self.bare, self.env, "rev-parse", "HEAD"
        ).stdout.strip()
        if bare_head != self.new_sha:
            raise PlantFailed(
                f"INVALID: bare remote still at {bare_head}, not {self.new_sha}"
            )

        still = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if still != self.old_sha:
            raise PlantFailed(
                "INVALID: clone origin/main moved before the hook ran"
            )
        if (self.clone / ".git" / "FETCH_HEAD").exists():
            raise PlantFailed(
                "INVALID: FETCH_HEAD reappeared before the hook ran"
            )
        return self

    def restale(self):
        """Put origin/main back behind the remote and drop FETCH_HEAD again."""
        git(self.clone, self.env, "update-ref", "refs/remotes/origin/main",
            self.old_sha)
        fetch_head = self.clone / ".git" / "FETCH_HEAD"
        if fetch_head.exists():
            fetch_head.unlink()
        now = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if now != self.old_sha:
            raise PlantFailed("INVALID: restale did not rewind origin/main")
        if fetch_head.exists():
            raise PlantFailed("INVALID: restale left FETCH_HEAD in place")

    def refresh(self):
        """Fetch until origin/main matches the remote. Prove it landed."""
        git(self.clone, self.env, "fetch", "--all", "-q")
        now = git(
            self.clone, self.env, "rev-parse", "origin/main"
        ).stdout.strip()
        if now != self.new_sha:
            raise PlantFailed(
                f"INVALID: refresh left origin/main at {now}, "
                f"not remote {self.new_sha}"
            )
        return self


def require_jq():
    if shutil.which("jq") is None:
        raise PlantFailed(
            "INVALID: jq is not on PATH; ruff-after-edit.sh cannot emit"
        )


def rev_parse_path(cwd, env, flag: str, sandbox: Path) -> Path:
    """Resolve ``git rev-parse <flag>`` to an absolute path inside *sandbox*.

    Used to tell a linked worktree's private git-dir from the clone's
    common dir without touching the real repository.
    """
    out = git(cwd, env, "rev-parse", flag).stdout.strip()
    if not out:
        raise PlantFailed(f"INVALID: git rev-parse {flag} was empty in {cwd}")
    path = Path(os.path.abspath(os.path.join(str(cwd), out)))
    sand = os.path.realpath(str(sandbox)) + os.sep
    real = os.path.realpath(str(path))
    if not real.startswith(sand):
        raise PlantFailed(
            f"INVALID: git rev-parse {flag} resolved to {path}, "
            f"outside sandbox {sandbox}"
        )
    if not path.exists():
        raise PlantFailed(f"INVALID: git rev-parse {flag} path missing: {path}")
    return path


def prove_linked_worktree(main: Path, linked: Path, env, sandbox: Path):
    """Prove this pair is a linked worktree of *main*, not a second clone.

    The one-stream finding is invisible when git-dir equals common-dir
    (the primary checkout). A fixture that failed to diverge is INVALID
    — later assertions would be answering a question about nothing.
    Also proves ``.git`` is a directory in the primary and a gitfile in
    the linked tree: that is the input a script which uses the common
    dir only when cwd looks like a main worktree gets wrong.
    """
    main_git = rev_parse_path(main, env, "--git-dir", sandbox)
    main_common = rev_parse_path(main, env, "--git-common-dir", sandbox)
    link_git = rev_parse_path(linked, env, "--git-dir", sandbox)
    link_common = rev_parse_path(linked, env, "--git-common-dir", sandbox)

    if os.path.realpath(str(main_common)) != os.path.realpath(str(link_common)):
        raise PlantFailed(
            "INVALID: linked worktree does not share the clone's common dir"
        )
    if os.path.realpath(str(main_git)) != os.path.realpath(str(main_common)):
        raise PlantFailed(
            "INVALID: primary git-dir is not the common dir; "
            "the fixture is not a normal checkout"
        )
    if os.path.realpath(str(link_git)) == os.path.realpath(str(link_common)):
        raise PlantFailed(
            "INVALID: linked worktree git-dir equals common-dir; "
            "this fixture cannot expose a split stream"
        )
    if not (main / ".git").is_dir():
        raise PlantFailed("INVALID: primary .git is not a directory")
    gitfile = linked / ".git"
    if gitfile.is_dir():
        raise PlantFailed(
            "INVALID: linked worktree .git is a directory, not a gitfile"
        )
    if not gitfile.is_file():
        raise PlantFailed("INVALID: linked worktree has no .git gitfile")
    return link_git, link_common
