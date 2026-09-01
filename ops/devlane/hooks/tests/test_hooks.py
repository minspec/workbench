"""Contract tests for the git-native crossing recorder (ops/devlane/hooks).

Written from the behavior contract only (branch repo/worktree-per-line,
behaviors H1-H7), then strengthened against a skeptic's surviving-mutant
report. The mutants were read as descriptions of faults, never as a
source of expectations: every assertion below traces to the contract.
Every test first asserts the three source files exist (install.sh,
post-checkout, post-commit), so a missing implementation fails as a plain
assertion with a message -- never as an import or collection error.

All git activity happens in throwaway repositories under tempfile, with
explicit user.name/user.email and fixed --date on every commit; the wall
clock is never an assertion input (the entry's "at" field is checked for
shape only). The real repo's .git, hooks, and stream file are never
touched: every resolved git common dir is asserted to live inside the
test's own tempdir before anything is read or written there.

Every fixture these tests corrupt -- a foreign hook, a symlinked hook, a
directory standing in the stream's place, pre-existing stream lines -- is
proved to have landed and to still be recognisably itself before the
behavior under test runs, so that no later assertion can answer a
question about nothing.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

# The package under test sits beside this tests/ directory, inside the
# worktree at ops/devlane/hooks/ -- resolved from this file's own location.
TESTS_DIR = Path(__file__).resolve().parent  # .../ops/devlane/hooks/tests
WORKTREE_ROOT = TESTS_DIR.parents[3]  # the repo/worktree root
HOOKS_SRC_DIR = WORKTREE_ROOT / "ops" / "devlane" / "hooks"
INSTALL_SH = HOOKS_SRC_DIR / "install.sh"
POST_CHECKOUT_SRC = HOOKS_SRC_DIR / "post-checkout"
POST_COMMIT_SRC = HOOKS_SRC_DIR / "post-commit"
COMMIT_MSG_SRC = HOOKS_SRC_DIR / "commit-msg"
#: commit-msg shells out to a checker in the WORKFLOW app, so a fixture
#: that stages only this package would make every commit fail — the hook
#: refuses when it cannot check, by design. Staging it here is not a
#: convenience: it is the real dependency, made visible.
TRAILER_CHECK_SRC = (WORKTREE_ROOT / "ops" / "devlane" / "workflow"
                     / "checks" / "commit_trailers.py")
SOURCES = (INSTALL_SH, POST_CHECKOUT_SRC, POST_COMMIT_SRC, COMMIT_MSG_SRC)
HOOK_SOURCES = (POST_CHECKOUT_SRC, POST_COMMIT_SRC, COMMIT_MSG_SRC)
HOOK_NAMES = ("post-checkout", "post-commit", "commit-msg")

STREAM_NAME = "claude-context-stream.jsonl"
WF = "Agent Under Test <noreply@example>"
# A perfectly legal WF_AGENT that is illegal inside a JSON string until
# it is escaped: it carries one double quote and one backslash.
TRICKY_WF = 'Odd " Agent \\ Name <noreply@example>'
FIXED_DATE_1 = "2026-01-02T03:04:05Z"
FIXED_DATE_2 = "2026-01-02T03:05:06Z"

# ISO8601 UTC with a literal Z suffix; fractional seconds allowed.
AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ENTRY_KEYS = ("at", "kind", "what", "detail", "agent", "worktree", "via")


class HookContractTest(unittest.TestCase):
    """Pins H1-H7 of the crossing-recorder behavior contract."""

    maxDiff = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hooks-contract-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()

    # ---------------------------------------------------------------- plumbing

    def require_sources(self):
        """The clear missing-file assertion: while the implementation does
        not exist, every test fails here, with a message, not on import."""
        for path in SOURCES:
            self.assertTrue(
                path.is_file(),
                f"hook package file does not exist yet (implementation "
                f"missing): {path}",
            )

    def env_for(self, wf_agent=WF, user="tester"):
        """A fully controlled environment: no global/system git config, a
        temp HOME, fixed commit dates. Pass None to omit WF_AGENT or USER
        entirely (LOGNAME is never set, so $USER is the only identity)."""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_DATE": FIXED_DATE_1,
            "GIT_COMMITTER_DATE": FIXED_DATE_1,
            "LC_ALL": "C",
        }
        if wf_agent is not None:
            env["WF_AGENT"] = wf_agent
        if user is not None:
            env["USER"] = user
        return env

    def run_cmd(self, argv, cwd, env=None, expect=0):
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env if env is not None else self.env_for(),
            capture_output=True,
            text=True,
            check=False,
        )
        if expect is not None:
            self.assertEqual(
                proc.returncode,
                expect,
                f"command {argv!r} in {cwd} exited {proc.returncode} "
                f"(wanted {expect})\nstdout: {proc.stdout}\n"
                f"stderr: {proc.stderr}",
            )
        return proc

    def git(self, cwd, *args, env=None, expect=0):
        return self.run_cmd(["git", *args], cwd, env=env, expect=expect)

    def make_repo(self, name):
        """A throwaway repo: explicit identity, fixed-date initial commit."""
        repo = self.tmp / name
        repo.mkdir()
        self.git(repo, "-c", "init.defaultBranch=main", "init", "-q")
        self.git(repo, "config", "user.name", "Test User")
        self.git(repo, "config", "user.email", "test@example.invalid")
        (repo / "file.txt").write_text("one\n")
        self.git(repo, "add", "file.txt")
        self.git(repo, "commit", "-q", "-m", "c1", "--date", FIXED_DATE_1)
        return repo

    def stage_pkg(self, repo):
        """Copy the hook package into the throwaway repo at its real
        relative path, proving each copy landed byte-for-byte, so that
        install.sh may resolve its hooks beside $0 or from the repo tree --
        both are faithful to 'install.sh run inside a repo'."""
        dest = repo / "ops" / "devlane" / "hooks"
        dest.mkdir(parents=True)
        check_dest = repo / "ops" / "devlane" / "workflow" / "checks"
        check_dest.mkdir(parents=True)
        shutil.copy2(str(TRAILER_CHECK_SRC), str(check_dest / TRAILER_CHECK_SRC.name))
        self.assertEqual(
            (check_dest / TRAILER_CHECK_SRC.name).read_bytes(),
            TRAILER_CHECK_SRC.read_bytes(),
            "staged copy of the trailer checker did not land intact",
        )
        for src in SOURCES:
            target = dest / src.name
            shutil.copy2(str(src), str(target))
            self.assertEqual(
                target.read_bytes(),
                src.read_bytes(),
                f"staged copy of {src.name} did not land intact",
            )
        return dest / "install.sh"

    def install(self, repo, *args, env=None, expect=0):
        """Run install.sh with `repo` as the working directory. `repo` may
        be a primary checkout or a linked worktree; the script is staged
        into whichever tree it is run from."""
        script = repo / "ops" / "devlane" / "hooks" / "install.sh"
        if not script.is_file():
            script = self.stage_pkg(repo)
        return self.run_cmd(["sh", str(script), *args], repo, env=env, expect=expect)

    def common_dir(self, cwd):
        """The clone's git common dir, asserted to be inside the sandbox so
        no test can ever touch the real repo's .git."""
        out = self.git(cwd, "rev-parse", "--git-common-dir").stdout.strip()
        common = Path(os.path.abspath(os.path.join(str(cwd), out)))
        self.assertTrue(
            os.path.realpath(str(common)).startswith(
                os.path.realpath(str(self.tmp)) + os.sep
            ),
            f"resolved git common dir {common} escapes the test sandbox {self.tmp}",
        )
        return common

    def hooks_dir(self, cwd):
        return self.common_dir(cwd) / "hooks"

    def stream_path(self, cwd):
        return self.common_dir(cwd) / STREAM_NAME

    def stream_lines(self, cwd):
        path = self.stream_path(cwd)
        if not path.is_file():
            return []
        return path.read_text().splitlines()

    def parse_entry(self, line):
        """One stream line must be a JSON object with the full schema:
        string fields at/kind/what/detail/agent/worktree/via, at in
        ISO8601Z shape, via 'git-hook', detail '' (the contract fixes it)."""
        try:
            entry = json.loads(line)
        except ValueError as exc:
            self.fail(f"stream line is not valid JSON: {line!r} ({exc})")
        self.assertIsInstance(
            entry, dict, f"stream line is not a JSON object: {line!r}"
        )
        for key in ENTRY_KEYS:
            self.assertIn(key, entry, f"entry lacks key {key!r}: {line!r}")
            self.assertIsInstance(
                entry[key], str, f"entry field {key!r} is not a string: {line!r}"
            )
        self.assertRegex(
            entry["at"],
            AT_RE,
            f"at is not ISO8601 UTC with a Z suffix: {entry['at']!r}",
        )
        self.assertEqual(entry["via"], "git-hook", f"via is not 'git-hook': {line!r}")
        self.assertEqual(
            entry["detail"], "", f"detail is not the empty string: {line!r}"
        )
        return entry

    def sole_new_entry(self, before, cwd):
        """Exactly one line was appended since `before`, and the stream is
        still append-only (earlier lines untouched). Returns the parsed
        new entry. Asserting the count first keeps every later field check
        non-vacuous."""
        after = self.stream_lines(cwd)
        self.assertEqual(
            after[: len(before)],
            before,
            "stream is not append-only: earlier lines changed or were lost "
            f"(before={before!r}, after={after!r})",
        )
        new = after[len(before) :]
        self.assertEqual(
            len(new),
            1,
            f"expected exactly one new stream line, got {len(new)}: {new!r}",
        )
        return self.parse_entry(new[0])

    # ------------------------------------------------------- guarded fixtures

    def assert_installed_hook(self, hooks, src, when):
        """An installed hook is present, byte-identical to the packaged
        one, and executable -- git silently ignores a non-executable
        hook, so the mode is part of 'installed', not a nicety."""
        installed = hooks / src.name
        self.assertTrue(
            installed.is_file(),
            f"{src.name} was not installed into {hooks} ({when})",
        )
        self.assertEqual(
            installed.read_bytes(),
            src.read_bytes(),
            f"installed {src.name} differs from the packaged hook ({when})",
        )
        self.assertTrue(
            installed.stat().st_mode & stat.S_IXUSR,
            f"installed {src.name} is not executable; git will never run it ({when})",
        )
        return installed

    def plant_foreign_hook(self, hooks, name):
        """Plant somebody else's hook of `name`, and prove the plant both
        landed and is distinguishable from the packaged hook -- otherwise
        a later 'was it clobbered?' check answers nothing."""
        hooks.mkdir(parents=True, exist_ok=True)
        packaged = (HOOKS_SRC_DIR / name).read_bytes()
        sentinel = (
            f"#!/bin/sh\n# pre-existing {name}, not ours\n"
            f"# sentinel: do-not-clobber-{name}\nexit 0\n"
        ).encode()
        dest = hooks / name
        dest.write_bytes(sentinel)
        dest.chmod(0o755)
        landed = dest.read_bytes()
        self.assertEqual(landed, sentinel, f"planted foreign {name} did not land")
        self.assertTrue(landed, f"planted foreign {name} landed empty")
        self.assertNotEqual(
            sentinel,
            packaged,
            f"planted foreign {name} is byte-identical to the packaged "
            "hook; this test cannot discriminate",
        )
        return sentinel

    def plant_stream_lines(self, cwd, count=2):
        """Seed the shared stream with earlier records, proving they
        landed, so that a later append can be told apart from a truncating
        overwrite. An empty stream makes those two indistinguishable."""
        path = self.stream_path(cwd)
        lines = [
            json.dumps(
                {
                    "at": FIXED_DATE_1,
                    "kind": "branch",
                    "what": f"earlier record {i} from another actor",
                    "detail": "",
                    "agent": "Someone Else <noreply@example>",
                    "worktree": "some-other-tree",
                    "via": "git-hook",
                }
            )
            for i in range(count)
        ]
        payload = "".join(line + "\n" for line in lines)
        self.assertTrue(payload, "stream plant payload is empty")
        path.write_text(payload)
        self.assertTrue(path.is_file(), "stream plant did not create the file")
        self.assertEqual(path.read_text(), payload, "stream plant did not land")
        self.assertEqual(
            path.stat().st_size,
            len(payload.encode()),
            "stream plant landed truncated",
        )
        self.assertEqual(
            self.stream_lines(cwd), lines, "planted stream lines do not read back"
        )
        return lines

    def commit_change(self, repo, text, message, env=None):
        """Make a commit and return the resulting short SHA. Only the
        commit itself carries `env`; staging and rev-parse fire no hook."""
        (repo / "file.txt").write_text(text)
        self.git(repo, "add", "file.txt")
        # A real commit in this repo carries a Source trailer, and the
        # commit-msg hook these fixtures install now requires one. A bare
        # `-m "primary commit"` was refused by the very gate under test.
        self.git(repo, "commit", "-q", "-m", message + "\n\nSource: original",
                 "--date", FIXED_DATE_2, env=env)
        short = self.git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        self.assertTrue(
            short,
            "could not resolve the new short SHA (guards the containment "
            "checks below from being vacuous)",
        )
        return short

    # ------------------------------------------------------------------- tests

    def test_h1_install_copies_both_hooks_and_reruns_quietly(self):
        """H1: install.sh run inside a repo copies both hooks into the
        clone's common hooks dir; a second run is a quiet success that
        leaves both hooks installed AND still executable."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.install(repo)
        hooks = self.hooks_dir(repo)
        for src in HOOK_SOURCES:
            self.assert_installed_hook(hooks, src, "after the first install")
        second = self.install(repo)  # idempotent: exit 0 asserted by install()
        self.assertEqual(
            second.stdout,
            "",
            f"second install was not quiet (stdout): {second.stdout!r}",
        )
        self.assertEqual(
            second.stderr,
            "",
            f"second install was not quiet (stderr): {second.stderr!r}",
        )
        # The rerun must not quietly disarm what the first run installed:
        # content AND mode are re-checked after the second run.
        for src in HOOK_SOURCES:
            self.assert_installed_hook(hooks, src, "after the idempotent rerun")

    def test_h1_install_from_a_linked_worktree_targets_the_common_hooks_dir(self):
        """H1+H6: install.sh run from a linked worktree still installs
        into the CLONE's common hooks dir -- not the worktree's private
        gitdir -- so the hooks fire from every worktree of the clone."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.git(repo, "branch", "beta")
        self.git(repo, "branch", "b-linked")
        self.git(repo, "branch", "b-target")
        wt2 = self.tmp / "second-tree"
        self.git(repo, "worktree", "add", str(wt2), "b-linked")
        common = self.common_dir(repo)
        self.assertEqual(
            os.path.realpath(str(self.common_dir(wt2))),
            os.path.realpath(str(common)),
            "the linked worktree does not share the clone's common dir; "
            "the fixture is not what this test needs",
        )
        # Install FROM the linked worktree.
        self.install(wt2)
        hooks = common / "hooks"
        for src in HOOK_SOURCES:
            self.assert_installed_hook(hooks, src, "installed from a linked worktree")
        private = common / "worktrees" / "second-tree" / "hooks"
        for name in HOOK_NAMES:
            self.assertFalse(
                (private / name).exists(),
                f"install from a linked worktree put {name} in the "
                f"per-worktree gitdir ({private}); git will not run it "
                "there and other worktrees never see it",
            )
        # The promise is not 'a file appeared' but 'the hook fires' -- and
        # it must fire from BOTH worktrees of the clone.
        before = self.stream_lines(repo)
        self.git(repo, "checkout", "-q", "beta", env=self.env_for(wf_agent=WF))
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["kind"],
            "branch",
            f"primary-worktree checkout did not record kind 'branch': {entry!r}",
        )
        self.assertEqual(
            entry["worktree"],
            "primary-clone",
            f"entry does not carry the primary worktree's name: {entry!r}",
        )
        before = self.stream_lines(repo)
        self.git(wt2, "checkout", "-q", "b-target", env=self.env_for(wf_agent=WF))
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["kind"],
            "branch",
            f"linked-worktree checkout did not record kind 'branch': {entry!r}",
        )
        self.assertIn(
            "b-target",
            entry["what"],
            f"what does not name the branch switched to: {entry['what']!r}",
        )
        self.assertEqual(
            entry["worktree"],
            "second-tree",
            f"entry does not carry the linked worktree's own name: {entry!r}",
        )

    def test_h2_existing_different_hook_survives_unless_forced(self):
        """H2: an existing different hook of the same name is not
        clobbered -- nonzero exit naming the conflict; --force replaces.
        Both names are planted, because a refusal that protects one name
        and overwrites the other still destroys somebody's hook."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        hooks = self.hooks_dir(repo)
        sentinels = {name: self.plant_foreign_hook(hooks, name) for name in HOOK_NAMES}
        proc = self.install(repo, expect=None)
        self.assertNotEqual(
            proc.returncode,
            0,
            "install over existing different hooks must exit nonzero\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        said = proc.stdout + proc.stderr
        self.assertIn(
            "post-checkout",
            said,
            "the conflict message does not name the conflicting hook",
        )
        for name in HOOK_NAMES:
            self.assertEqual(
                (hooks / name).read_bytes(),
                sentinels[name],
                f"existing {name} was clobbered by a non-forced install "
                "that had already refused to proceed",
            )
        self.install(repo, "--force")  # exit 0 asserted by install()
        for src in HOOK_SOURCES:
            self.assert_installed_hook(hooks, src, "after the forced install")

    def test_h2_each_hook_name_is_protected_individually(self):
        """H2, per name: whichever single hook is already present and
        different, the non-forced install refuses, names THAT hook, and
        leaves it byte-for-byte intact."""
        self.require_sources()
        for name in HOOK_NAMES:
            with self.subTest(hook=name):
                repo = self.make_repo(f"clone-{name}")
                hooks = self.hooks_dir(repo)
                sentinel = self.plant_foreign_hook(hooks, name)
                proc = self.install(repo, expect=None)
                self.assertNotEqual(
                    proc.returncode,
                    0,
                    f"install over an existing different {name} must exit "
                    f"nonzero\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
                )
                self.assertIn(
                    name,
                    proc.stdout + proc.stderr,
                    f"the conflict message does not name {name}",
                )
                self.assertEqual(
                    (hooks / name).read_bytes(),
                    sentinel,
                    f"existing {name} was clobbered by a non-forced install",
                )

    def test_h2_force_replacing_a_symlinked_hook_stays_inside_the_hooks_dir(self):
        """H2 containment: --force replaces the hook IN the hooks dir. A
        hook that happens to be a symlink pointing elsewhere must not turn
        the install into a write to that other file: the replacement lands
        as a regular file in the hooks dir and the symlink's target
        outside it is untouched."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        hooks = self.hooks_dir(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        outside_dir = self.tmp / "outside"
        outside_dir.mkdir()
        outside = outside_dir / "foreign-target"
        sentinel = b"#!/bin/sh\n# somebody else's file, outside the hooks dir\nexit 0\n"
        outside.write_bytes(sentinel)
        outside.chmod(0o755)
        link = hooks / "post-checkout"
        os.symlink(str(outside), str(link))
        # Prove the plant landed and is still recognisably itself.
        self.assertTrue(link.is_symlink(), "symlinked-hook plant did not land")
        self.assertEqual(
            os.path.realpath(str(link)),
            os.path.realpath(str(outside)),
            "planted symlink does not point at the outside target",
        )
        self.assertEqual(
            outside.read_bytes(), sentinel, "outside target was not planted intact"
        )
        self.assertNotEqual(
            sentinel,
            POST_CHECKOUT_SRC.read_bytes(),
            "outside target is byte-identical to the packaged hook; this "
            "test cannot discriminate",
        )
        self.install(repo, "--force")  # exit 0 asserted by install()
        self.assertEqual(
            outside.read_bytes(),
            sentinel,
            f"--force wrote through the symlink and overwrote {outside}, a "
            "file outside the hooks dir that install.sh was never asked to "
            "touch",
        )
        self.assertFalse(
            link.is_symlink(),
            "the hooks dir still holds a symlink after --force; the "
            "replacement did not land in the hooks dir",
        )
        self.assert_installed_hook(
            hooks, POST_CHECKOUT_SRC, "after --force over a symlinked hook"
        )

    def test_h3_checkout_appends_one_branch_entry(self):
        """Scenario: a branch checkout is recorded with its actor and worktree

        H3: after install, git checkout <other-branch> appends exactly
        one valid JSON line: kind 'branch', what naming the branch, agent
        == $WF_AGENT, via 'git-hook', correct worktree."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.git(repo, "branch", "beta")
        self.install(repo)
        before = self.stream_lines(repo)
        self.git(repo, "checkout", "-q", "beta", env=self.env_for(wf_agent=WF))
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(entry["kind"], "branch", f"kind is not 'branch': {entry!r}")
        self.assertIn(
            "beta",
            entry["what"],
            f"what does not name the branch switched to: {entry['what']!r}",
        )
        self.assertEqual(entry["agent"], WF, f"agent is not $WF_AGENT: {entry!r}")
        self.assertEqual(
            entry["worktree"],
            "primary-clone",
            f"worktree is not the basename of the toplevel: {entry!r}",
        )

    def test_h3_entries_stay_valid_json_for_a_quoting_agent(self):
        """H3/H4 schema: the stream is JSONL, so a WF_AGENT carrying a
        double quote and a backslash -- both legal in a name -- must come
        back out of json.loads as exactly that string, from a checkout
        entry and from a commit entry alike."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.git(repo, "branch", "beta")
        self.install(repo)
        self.assertIn('"', TRICKY_WF, "fixture agent has no double quote")
        self.assertIn("\\", TRICKY_WF, "fixture agent has no backslash")
        env = self.env_for(wf_agent=TRICKY_WF)
        before = self.stream_lines(repo)
        self.git(repo, "checkout", "-q", "beta", env=env)
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["agent"],
            TRICKY_WF,
            "checkout entry did not round-trip the agent string through "
            f"JSON: {entry!r}",
        )
        self.assertEqual(entry["kind"], "branch", f"kind is not 'branch': {entry!r}")
        before = self.stream_lines(repo)
        self.commit_change(repo, "escaped\n", "c-escaped", env=env)
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["agent"],
            TRICKY_WF,
            f"commit entry did not round-trip the agent string through JSON: {entry!r}",
        )
        self.assertEqual(entry["kind"], "head", f"kind is not 'head': {entry!r}")

    def test_h4_commit_appends_head_entry_with_new_short_sha(self):
        """H4: git commit APPENDS kind 'head' with the new short SHA in
        what -- earlier records written by other actors survive it."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.install(repo)
        planted = self.plant_stream_lines(repo)
        before = self.stream_lines(repo)
        self.assertEqual(
            before,
            planted,
            "the stream does not hold the planted earlier records; an "
            "append could not be told from an overwrite",
        )
        short = self.commit_change(repo, "two\n", "c2")
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(entry["kind"], "head", f"kind is not 'head': {entry!r}")
        self.assertIn(
            short,
            entry["what"],
            f"what does not contain the new short SHA {short}: {entry['what']!r}",
        )
        self.assertEqual(
            entry["worktree"],
            "primary-clone",
            f"worktree is not the basename of the toplevel: {entry!r}",
        )
        after = self.stream_lines(repo)
        self.assertEqual(
            after[: len(planted)],
            planted,
            f"the commit did not append: earlier shared history is gone "
            f"(planted={planted!r}, after={after!r})",
        )

    def test_h5_unwritable_stream_never_breaks_git(self):
        """Scenario: a hook failure never breaks git

        H5: with the stream file unwritable, checkout and commit still
        exit 0 -- the record is lost, the operation is not. Lost means
        lost: the obstruction is still standing afterwards and no stream
        file was conjured up anywhere else."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.git(repo, "branch", "beta")
        self.install(repo)
        stream = self.stream_path(repo)
        if stream.is_file():
            stream.unlink()
        # A directory at the stream path defeats appending for every uid,
        # root included (chmod would not stop root).
        stream.mkdir()
        self.assertTrue(stream.is_dir(), "unwritable-stream plant did not land")
        self.assertEqual(
            list(stream.iterdir()), [], "unwritable-stream plant is not empty"
        )
        # exit 0 asserted by git(); a failing post-checkout hook becomes
        # git checkout's own exit status, so this line is the pin.
        self.git(repo, "checkout", "-q", "beta")
        on = self.git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(on, "beta", "checkout did not actually land on beta")
        old = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        self.commit_change(repo, "three\n", "c3")
        new = self.git(repo, "rev-parse", "HEAD").stdout.strip()
        self.assertTrue(old and new, "could not resolve HEAD around the commit")
        self.assertNotEqual(new, old, "commit exited 0 but did not create a new commit")
        # The obstruction is the user's: a hook may not clear it to get
        # its record written.
        self.assertTrue(
            stream.is_dir(),
            f"the obstruction at {stream} did not survive; a hook removed "
            "what it could not write to",
        )
        self.assertEqual(
            list(stream.iterdir()),
            [],
            f"something was written inside the obstruction at {stream}",
        )
        strays = sorted(str(p) for p in self.tmp.rglob(STREAM_NAME) if p.is_file())
        self.assertEqual(
            strays,
            [],
            f"the record was not lost: a stream file materialised at {strays}",
        )

    def test_h6_second_worktree_appends_to_the_shared_stream(self):
        """H6: a checkout inside a second git worktree of the same clone
        appends to the SAME stream file, with that worktree's own
        worktree value."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.git(repo, "branch", "b-linked")
        self.git(repo, "branch", "b-target")
        self.install(repo)
        wt2 = self.tmp / "second-tree"
        self.git(repo, "worktree", "add", str(wt2), "b-linked")
        before = self.stream_lines(repo)  # snapshot AFTER worktree add's checkout
        self.git(wt2, "checkout", "-q", "b-target", env=self.env_for(wf_agent=WF))
        # Read through the PRIMARY clone: growth here IS the shared-stream pin.
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(entry["kind"], "branch", f"kind is not 'branch': {entry!r}")
        self.assertIn(
            "b-target",
            entry["what"],
            f"what does not name the branch switched to: {entry['what']!r}",
        )
        self.assertEqual(
            entry["worktree"],
            "second-tree",
            f"entry does not carry the second worktree's own name: {entry!r}",
        )
        per_wt = self.common_dir(repo) / "worktrees" / "second-tree" / STREAM_NAME
        self.assertFalse(
            per_wt.exists(),
            "a stream file appeared in the per-worktree gitdir; the stream "
            "must be shared at the common dir only",
        )

    def test_h7_agent_falls_back_to_user_then_unknown(self):
        """Scenario: an unset WF_AGENT falls back to the system user

        H7: with WF_AGENT unset the entry is still written, agent
        falling back to $USER, and to 'unknown' when USER is unset too."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.git(repo, "branch", "beta")
        self.git(repo, "branch", "gamma")
        self.install(repo)
        before = self.stream_lines(repo)
        self.git(
            repo,
            "checkout",
            "-q",
            "beta",
            env=self.env_for(wf_agent=None, user="fallbackuser"),
        )
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["agent"],
            "fallbackuser",
            f"with WF_AGENT unset, agent did not fall back to $USER: {entry!r}",
        )
        before = self.stream_lines(repo)
        self.git(
            repo,
            "checkout",
            "-q",
            "gamma",
            env=self.env_for(wf_agent=None, user=None),
        )
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["agent"],
            "unknown",
            f"with WF_AGENT and USER both unset, agent is not 'unknown': {entry!r}",
        )

    def test_h7_commit_agent_falls_back_to_user_then_unknown(self):
        """H7 for the other hook: the identity fallback is a property of
        every entry, so a commit with WF_AGENT unset records $USER, and
        'unknown' when USER is unset too."""
        self.require_sources()
        repo = self.make_repo("primary-clone")
        self.install(repo)
        before = self.stream_lines(repo)
        short = self.commit_change(
            repo,
            "two\n",
            "c2",
            env=self.env_for(wf_agent=None, user="fallbackuser"),
        )
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(entry["kind"], "head", f"kind is not 'head': {entry!r}")
        self.assertIn(
            short,
            entry["what"],
            f"what does not contain the new short SHA {short}: {entry['what']!r}",
        )
        self.assertEqual(
            entry["agent"],
            "fallbackuser",
            "with WF_AGENT unset, the commit entry's agent did not fall "
            f"back to $USER: {entry!r}",
        )
        before = self.stream_lines(repo)
        self.commit_change(
            repo,
            "three\n",
            "c3",
            env=self.env_for(wf_agent=None, user=None),
        )
        entry = self.sole_new_entry(before, repo)
        self.assertEqual(
            entry["agent"],
            "unknown",
            "with WF_AGENT and USER both unset, the commit entry's agent "
            f"is not 'unknown': {entry!r}",
        )


class ReviewFindingsContractTest(HookContractTest):
    """PR #25 Codex-review findings, pinned red-first on this head."""

    def test_configured_hookspath_is_refused_not_silently_bypassed(self):
        self.require_sources()
        repo = self.make_repo("hookspath")
        self.git(repo, "config", "core.hooksPath", "custom-hooks")
        proc = self.install(repo, expect=1)
        self.assertIn(
            "core.hooksPath",
            proc.stdout + proc.stderr,
            "the refusal must name core.hooksPath so the operator knows why",
        )
        self.assertFalse(
            (self.hooks_dir(repo) / "post-checkout").exists(),
            "install wrote hooks git will never run — a successful inert install",
        )

    def test_dangling_symlink_hook_is_not_clobbered_without_force(self):
        self.require_sources()
        repo = self.make_repo("dangle")
        hooks = self.hooks_dir(repo)
        hooks.mkdir(parents=True, exist_ok=True)
        dest = hooks / "post-checkout"
        dest.symlink_to(self.tmp / "gone-target")
        self.assertTrue(dest.is_symlink(), "the dangling-symlink plant did not land")
        self.assertFalse(dest.exists(), "the symlink plant is not dangling")
        self.install(repo, expect=1)
        self.assertTrue(
            dest.is_symlink() and os.readlink(str(dest)).endswith("gone-target"),
            "a non-forced install destroyed a foreign dangling symlink",
        )
        self.install(repo, "--force", expect=0)
        self.assertTrue(
            dest.is_file() and not dest.is_symlink(),
            "--force must replace the dangling symlink with the packaged hook",
        )

    def test_control_characters_in_agent_never_break_the_stream(self):
        self.require_sources()
        repo = self.make_repo("controls")
        self.install(repo, expect=0)
        agent = "Line\nBreak\tAgent <noreply@example>"
        self.assertIn("\n", agent, "the control-character plant did not land")
        env = self.env_for(wf_agent=agent)
        self.git(repo, "checkout", "-q", "-b", "side", env=env)
        lines = self.stream_lines(repo)
        self.assertEqual(
            len(lines),
            1,
            "one checkout crossing must yield exactly one stream record; "
            f"a raw newline split it: {lines!r}",
        )
        entry = self.parse_entry(lines[0])
        for field in ("agent", "worktree"):
            self.assertFalse(
                any(ord(ch) < 0x20 for ch in entry[field]),
                f"raw control characters leaked into the {field} field",
            )


class SecondReviewFindingsContractTest(HookContractTest):
    """PR #25 second Codex review (head f5824f5), pinned red-first."""

    def test_explicitly_empty_hookspath_is_also_refused(self):
        self.require_sources()
        repo = self.make_repo("empty-hookspath")
        self.git(repo, "config", "core.hooksPath", "")
        self.assertEqual(
            self.git(repo, "config", "--get", "core.hooksPath").stdout,
            "\n",
            "the empty-hooksPath plant did not land",
        )
        proc = self.install(repo, expect=1)
        self.assertIn(
            "core.hooksPath",
            proc.stdout + proc.stderr,
            "an explicitly empty core.hooksPath must be refused by name",
        )
        self.assertFalse(
            (self.hooks_dir(repo) / "post-checkout").exists(),
            "install wrote hooks git will never run (empty hooksPath)",
        )

    def test_orphan_checkout_crossing_is_recorded(self):
        self.require_sources()
        repo = self.make_repo("orphan")
        self.install(repo, expect=0)
        env = self.env_for()
        self.git(repo, "checkout", "-q", "--orphan", "fresh-start", env=env)
        lines = self.stream_lines(repo)
        self.assertEqual(
            len(lines),
            1,
            "an orphan checkout is a branch crossing and must be recorded",
        )
        entry = self.parse_entry(lines[0])
        self.assertIn(
            "fresh-start",
            entry["what"],
            "the crossing record must name the orphan branch",
        )


if __name__ == "__main__":
    unittest.main()
