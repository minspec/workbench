"""A record commit is stamped with the owner's identity, not the machine's.

Written from AGENTS.md §Attribution — author and committer stay the
owner's identity, ``xormania <127287135+xormania@users.noreply.github.com>``
— and CONTRACT.md §Dispatch The record (lines 239-247): one record
commit on the lineage branch, ``git commit --only``, message
``dispatch: record <id>``.

Observed before this test existed: record commits carried the
machine's git identity ('machine project <machine@host.localdomain>')
as author and committer instead of the owner's.
"""

from __future__ import annotations

import os
import subprocess

import launch_support as ls

OWNER_IDENT = "xormania <127287135+xormania@users.noreply.github.com>"
OWNER_NAME = "xormania"
OWNER_EMAIL = "127287135+xormania@users.noreply.github.com"

DECOY_AUTHOR_NAME = "decoy-author"
DECOY_AUTHOR_EMAIL = "decoy-author@example.invalid"
DECOY_COMMITTER_NAME = "decoy-committer"
DECOY_COMMITTER_EMAIL = "decoy-committer@example.invalid"
DECOY_CONFIG_NAME = "decoy-config"
DECOY_CONFIG_EMAIL = "decoy-config@example.invalid"


class RecordCommitCarriesTheOwnersIdentity(ls._TempLaunch):
    """Author and committer of the record commit are the owner's identity.

    A launcher that inherits the process git identity, the repo
    ``user.name``, or ``WF_AGENT`` still fails: those are planted as
    decoys, and the owner's identity is none of them.
    """

    def _git_as_process(self, *args):
        """git with the process environment, the way a child commit sees it."""
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )

    def _plant_decoy_identities(self):
        """Every identity source git would consult is a non-owner decoy."""
        plants = {
            "GIT_AUTHOR_NAME": DECOY_AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": DECOY_AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": DECOY_COMMITTER_NAME,
            "GIT_COMMITTER_EMAIL": DECOY_COMMITTER_EMAIL,
        }
        for key, value in plants.items():
            os.environ[key] = value
            self.env[key] = value
            self.assertEqual(os.environ[key], value)
            self.assertEqual(self.env[key], value)
            self.assertNotEqual(value, OWNER_NAME)
            self.assertNotEqual(value, OWNER_EMAIL)

        self._git("config", "user.name", DECOY_CONFIG_NAME)
        self._git("config", "user.email", DECOY_CONFIG_EMAIL)
        self.assertEqual(
            self._git("config", "user.name").stdout.strip(),
            DECOY_CONFIG_NAME,
        )
        self.assertEqual(
            self._git("config", "user.email").stdout.strip(),
            DECOY_CONFIG_EMAIL,
        )

        author = self._git_as_process("var", "GIT_AUTHOR_IDENT")
        committer = self._git_as_process("var", "GIT_COMMITTER_IDENT")
        self.assertEqual(author.returncode, 0, author.stderr)
        self.assertEqual(committer.returncode, 0, committer.stderr)
        self.assertIn(DECOY_AUTHOR_NAME, author.stdout)
        self.assertIn(DECOY_AUTHOR_EMAIL, author.stdout)
        self.assertIn(DECOY_COMMITTER_NAME, committer.stdout)
        self.assertIn(DECOY_COMMITTER_EMAIL, committer.stdout)
        self.assertNotIn(OWNER_NAME, author.stdout)
        self.assertNotIn(OWNER_EMAIL, author.stdout)
        self.assertNotIn(OWNER_NAME, committer.stdout)
        self.assertNotIn(OWNER_EMAIL, committer.stdout)
        self.assertNotEqual(ls.AGENT, OWNER_IDENT)
        self.assertEqual(os.environ.get("WF_AGENT"), ls.AGENT)

    def _record_commit_sha(self, rec):
        rel = f".dev/records/dispatches/{rec['id']}.json"
        path = self.repo / rel
        self.assertTrue(path.is_file(), f"record file missing: {path}")
        sha = self._git(
            "log", "-1", "--format=%H", "--", rel,
        ).stdout.strip()
        self.assertRegex(
            sha, r"^[0-9a-f]{40}$",
            f"record file is not in any commit: {rel}",
        )
        self.assertNotEqual(sha, self.ref)
        return sha

    def _ident_of(self, sha, fmt):
        ident = self._git(
            "log", "-1", f"--format={fmt}", sha,
        ).stdout.strip()
        self.assertTrue(ident, f"empty identity from {fmt} on {sha}")
        self.assertIn("<", ident)
        self.assertIn(">", ident)
        return ident

    def test_the_record_commit_author_is_the_owners_identity(self):
        self._plant_decoy_identities()
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        sha = self._record_commit_sha(rec)
        author = self._ident_of(sha, "%an <%ae>")
        self.assertEqual(author, OWNER_IDENT)
        self.assertNotEqual(
            author,
            f"{DECOY_AUTHOR_NAME} <{DECOY_AUTHOR_EMAIL}>",
        )
        self.assertNotEqual(author, ls.AGENT)

    def test_the_record_commit_committer_is_the_owners_identity(self):
        self._plant_decoy_identities()
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        sha = self._record_commit_sha(rec)
        committer = self._ident_of(sha, "%cn <%ce>")
        self.assertEqual(committer, OWNER_IDENT)
        self.assertNotEqual(
            committer,
            f"{DECOY_COMMITTER_NAME} <{DECOY_COMMITTER_EMAIL}>",
        )
        self.assertNotEqual(committer, ls.AGENT)
