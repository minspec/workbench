#!/usr/bin/env python3
"""Build task-sized Git snapshots without changing the source checkout.

Smaller fileset is only useful when it still answers the same question as
the complete tree. The functions here therefore keep selection,
materialization, and parity checking together: a missing requested file is
an error, snapshot provenance travels with the files, and a command that
never started cannot be mistaken for a matching result.

All Git access is through object-reading commands. In particular, this
module never checks out a ref or asks Git to repair the caller's worktree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

_MANIFEST_NAME = "FILESET.md"
_DIFF_NAME = "FILESET.diff"
_RESERVED_NAMES = frozenset({_MANIFEST_NAME, _DIFF_NAME})
_PATH_NEIGHBOUR = rb"A-Za-z0-9._/-"


class FilesetError(Exception):
    """A fileset that would have misled the task. Refuse, never repair."""


class _UsageError(Exception):
    """An argparse refusal that main can translate to the CLI contract."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        raise _UsageError(f"{self.prog}: error: {message}")


def _as_text_path(value, field):
    try:
        return os.fsdecode(os.fspath(value))
    except TypeError as exc:
        raise FilesetError(f"{field} must be a path") from exc


def _git(repo, *args):
    repo_path = _as_text_path(repo, "repo")
    git_env = os.environ.copy()
    # A read from a partial clone may otherwise fetch a missing object and
    # turn local fileset construction into an undeclared network operation.
    git_env["GIT_NO_LAZY_FETCH"] = "1"
    git_env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            ["git", "-C", repo_path, *args],
            env=git_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (OSError, ValueError) as exc:
        raise FilesetError(f"git could not run for {repo_path!r}: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        if not detail:
            detail = f"git exited {completed.returncode} without an error message"
        raise FilesetError(detail)
    return completed.stdout


def _commit(repo, ref):
    if ref is None:
        raise FilesetError("ref is required")
    ref_text = str(ref)
    if not ref_text:
        raise FilesetError("ref is empty")
    raw = _git(
        repo, "rev-parse", "--verify", "--end-of-options",
        f"{ref_text}^{{commit}}",
    )
    sha = raw.decode("ascii", "strict").strip()
    if not sha or not all(character in "0123456789abcdefABCDEF" for character in sha):
        raise FilesetError(f"git resolved {ref_text!r} to an invalid object name")
    return sha.lower()


def _repo_path(value, field="include path"):
    path = _as_text_path(value, field)
    pure = PurePosixPath(path)
    if not path or path == "." or pure.is_absolute() or ".." in pure.parts:
        raise FilesetError(f"{field} {path!r} is not a repo-relative path")
    return pure.as_posix()


def _tree(repo, sha):
    """Return path -> (mode, type, object id) for one committed tree."""
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", sha)
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.split(b" ", 2)
        except ValueError as exc:
            raise FilesetError("git ls-tree returned an unreadable record") from exc
        path = os.fsdecode(raw_path)
        normalized = _repo_path(path, "path in the committed tree")
        if normalized != path:
            raise FilesetError(f"git tree path {path!r} is not canonical")
        entries[path] = (
            mode.decode("ascii"), kind.decode("ascii"),
            object_id.decode("ascii"),
        )
    return entries


def _diff(repo, base_sha, ref_sha):
    # Hooks, textconv, and external diff commands would turn a read into
    # caller-controlled execution and could mutate the checkout indirectly.
    return _git(
        repo, "diff", "--no-ext-diff", "--no-textconv",
        base_sha, ref_sha, "--",
    )


def _diff_body(diff_text):
    """The diff's lines with their leading +/-/space marker removed.

    A removal marker is `-`, which is also a legal path character, so a
    path named at the START of a removed line arrives as `-config.toml`
    and the boundary rule below refuses it — while the same reference on
    an ADDED line matches, because `+` is not a path character. That
    asymmetry made a derivation silently miss a file (Grok's
    test_a_deletion_whose_hunk_names_a_survivor_keeps_the_survivor_only,
    written from the contract without sight of this code). Stripping the
    marker restores each line's own text before boundaries are judged.
    """
    return b"\n".join(
        line[1:] if line[:1] in (b"+", b"-", b" ") else line
        for line in diff_text.split(b"\n"))


def _path_is_named(diff_text, path):
    raw_path = os.fsencode(path)
    # Boundaries keep a short tracked name such as `a` from matching every
    # occurrence of that letter, while quotes, backticks, and line suffixes
    # remain valid ways for source text to name a path.
    pattern = (
        rb"(?<![" + _PATH_NEIGHBOUR + rb"])(?:\./)?"
        + re.escape(raw_path)
        + rb"(?![" + _PATH_NEIGHBOUR + rb"])")
    return re.search(pattern, diff_text) is not None


def derive(repo, ref, base):
    ref_sha = _commit(repo, ref)
    base_sha = _commit(repo, base)
    entries = _tree(repo, ref_sha)
    files_at_ref = {
        path for path, (_, kind, _) in entries.items() if kind == "blob"
    }

    changed_raw = _git(
        repo, "diff", "--name-only", "-z", "--no-ext-diff",
        "--no-textconv", base_sha, ref_sha, "--",
    )
    changed = {
        _repo_path(os.fsdecode(path), "changed path")
        for path in changed_raw.split(b"\0") if path
    }
    diff_text = _diff(repo, base_sha, ref_sha)

    # Comparing against the committed file list recognizes references in
    # any source language without guessing which quoting syntax it uses.
    searchable = _diff_body(diff_text)
    named = {path for path in files_at_ref
             if _path_is_named(searchable, path)}
    return sorted((changed | named) & files_at_ref)


def _source_root(repo):
    try:
        top = _git(repo, "rev-parse", "--show-toplevel")
    except FilesetError:
        # A bare repository has no checkout to protect, but its object store
        # is still source state and must not become the output directory.
        return Path(_as_text_path(repo, "repo")).resolve()
    return Path(os.fsdecode(top).strip()).resolve()


def _inside(path, directory):
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _prepare_destination(repo, into):
    display = _as_text_path(into, "into")
    root = Path(display)
    resolved = root.resolve(strict=False)
    if _inside(resolved, _source_root(repo)):
        raise FilesetError(
            f"snapshot destination {display!r} is inside the source repo")
    if root.is_symlink():
        raise FilesetError(f"snapshot destination {display!r} is a symlink")
    if root.exists():
        if not root.is_dir():
            raise FilesetError(f"snapshot destination {display!r} is not a directory")
        try:
            if next(root.iterdir(), None) is not None:
                raise FilesetError(
                    f"snapshot destination {display!r} is not empty")
        except OSError as exc:
            raise FilesetError(
                f"snapshot destination {display!r} cannot be inspected: {exc}") from exc
    else:
        try:
            root.mkdir(parents=True)
        except OSError as exc:
            raise FilesetError(
                f"snapshot destination {display!r} cannot be created: {exc}") from exc
    return root, display


def _selected_files(repo, ref_sha, include, base_sha, whole):
    entries = _tree(repo, ref_sha)
    if whole and include is not None:
        raise FilesetError("whole and include are mutually exclusive")
    if whole:
        files = sorted(
            path for path, (_, kind, _) in entries.items() if kind == "blob")
    else:
        if include is None:
            if base_sha is None:
                raise FilesetError("include or base is required unless whole is true")
            files = derive(repo, ref_sha, base_sha)
        else:
            if isinstance(include, (str, bytes, os.PathLike)):
                raise FilesetError(
                    "include must be a collection of repo-relative paths")
            try:
                files = sorted({_repo_path(path) for path in include})
            except TypeError as exc:
                raise FilesetError(
                    "include must be a collection of repo-relative paths") from exc

    if not files:
        raise FilesetError("the include set is empty")
    for path in files:
        entry = entries.get(path)
        if entry is None:
            raise FilesetError(f"include path {path!r} does not exist at {ref_sha}")
        if entry[1] != "blob":
            raise FilesetError(f"include path {path!r} is not a file at {ref_sha}")
        if PurePosixPath(path).parts[0] in _RESERVED_NAMES:
            raise FilesetError(
                f"include path {path!r} collides with snapshot metadata")
    return files, entries


def _write_file(repo, root, path, entry):
    mode, _, object_id = entry
    data = _git(repo, "cat-file", "blob", object_id)
    target = root.joinpath(*PurePosixPath(path).parts)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            if b"\0" in data:
                raise FilesetError(f"symlink {path!r} has an invalid target")
            os.symlink(os.fsdecode(data), target)
        else:
            with target.open("xb") as stream:
                stream.write(data)
            # A runnable script must remain runnable for parity to mean the
            # same thing as it did in the committed tree.
            target.chmod(0o755 if int(mode, 8) & 0o111 else 0o644)
    except FilesetError:
        raise
    except (OSError, ValueError) as exc:
        raise FilesetError(f"could not write {path!r}: {exc}") from exc
    return len(data)


def _manifest_text(ref_sha, base_sha, files, whole):
    lines = [
        "# Fileset snapshot",
        "",
        f"Ref: `{ref_sha}`",
        f"Base: `{base_sha if base_sha is not None else 'None'}`",
        f"Whole: `{'true' if whole else 'false'}`",
        "",
        "## Included paths",
        "",
    ]
    # JSON string literals keep unusual path characters on one unambiguous
    # line while leaving ordinary paths easy for a task to read.
    lines.extend(f"- {json.dumps(path, ensure_ascii=True)}" for path in files)
    return "\n".join(lines) + "\n"


def snapshot(repo, ref, into, *, include=None, base=None, whole=False):
    ref_sha = _commit(repo, ref)
    base_sha = _commit(repo, base) if base is not None else None
    files, entries = _selected_files(
        repo, ref_sha, include, base_sha, bool(whole))
    root, root_display = _prepare_destination(repo, into)

    byte_count = 0
    for path in files:
        byte_count += _write_file(repo, root, path, entries[path])

    diff_path = None
    if base_sha is not None:
        diff_path = str(root / _DIFF_NAME)
        try:
            (root / _DIFF_NAME).write_bytes(_diff(repo, base_sha, ref_sha))
        except OSError as exc:
            raise FilesetError(f"could not write {_DIFF_NAME}: {exc}") from exc

    manifest_path = str(root / _MANIFEST_NAME)
    try:
        (root / _MANIFEST_NAME).write_text(
            _manifest_text(ref_sha, base_sha, files, bool(whole)),
            encoding="utf-8",
        )
    except OSError as exc:
        raise FilesetError(f"could not write {_MANIFEST_NAME}: {exc}") from exc

    return {
        "ref": ref_sha,
        "base": base_sha,
        "root": root_display,
        "files": files,
        "bytes": byte_count,
        "diff": diff_path,
        "manifest": manifest_path,
        "whole": bool(whole),
    }


def _command_result(argv, cwd):
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except (OSError, ValueError) as exc:
        return None, f"command could not start: {exc}"
    output = completed.stdout.decode("utf-8", "replace")
    return (completed.returncode, completed.stdout), output


def parity(repo, pruned, argv, *, ref=None):
    if isinstance(argv, (str, bytes, os.PathLike)):
        raise FilesetError("argv must be a non-empty argument vector")
    try:
        command = list(argv)
    except TypeError as exc:
        raise FilesetError("argv must be a non-empty argument vector") from exc
    if not command:
        raise FilesetError("argv must be a non-empty argument vector")

    pruned_path = Path(_as_text_path(pruned, "pruned"))
    if not pruned_path.is_dir():
        raise FilesetError(f"pruned snapshot {str(pruned_path)!r} is not a directory")
    ref_to_run = "HEAD" if ref is None else ref

    with tempfile.TemporaryDirectory(prefix="fileset-whole-") as temporary:
        whole_root = Path(temporary) / "tree"
        snapshot(repo, ref_to_run, whole_root, whole=True)
        full_result, full_output = _command_result(command, whole_root)
        pruned_result, pruned_output = _command_result(command, pruned_path)

    if full_result is None and pruned_result is None:
        raise FilesetError(
            "command could not run in either snapshot: "
            f"full={full_output}; pruned={pruned_output}")
    return full_result == pruned_result, full_output, pruned_output


def _parser():
    parser = _Parser(prog="fileset.py")
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("snapshot")
    make.add_argument("ref")
    make.add_argument("--into", required=True)
    make.add_argument("--base")
    make.add_argument("--include", nargs="+")
    make.add_argument("--whole", action="store_true")
    return parser


def main(argv=None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except _UsageError as exc:
        print(exc, file=sys.stderr)
        return 64
    except SystemExit as exc:
        # argparse owns --help output; the CLI still returns rather than
        # terminating when main is called as a library function.
        return int(exc.code)

    try:
        manifest = snapshot(
            Path.cwd(), arguments.ref, arguments.into,
            include=arguments.include, base=arguments.base,
            whole=arguments.whole,
        )
    except FilesetError as exc:
        print(f"fileset.py: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
