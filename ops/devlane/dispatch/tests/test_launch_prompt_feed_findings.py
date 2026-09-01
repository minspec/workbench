"""Red pins for dispatch/prompt-feed review findings F1, F3-F10, F12.

Authored from CONTRACT.md §Dispatch (Collect, Isolation, The job
directory, Watching, Template values) plus the findings at
``.dev/records/dispatches/20260828T221050Z-review-claude-4db38d.json``.
F2 is out of scope (not reproduced). F11 names CONTRACT.md text to
correct, not a test.

  F1   read role that commits → envelope invalid (head must equal ref_sha)
  F3   claude --add-dir must not grant the job-directory evidence files
  F4   launcher snapshot commit identity ignores GIT_AUTHOR_* env
  F5   timed-out codex write is not committed by the launcher
  F6   close of a finished uncollected job reads the stream
  F7   narration after a pretty-printed envelope is tolerated
  F8   a runtime note keeps the envelope-parse reason
  F9   stale origin/dev is not preferred; an empty {diff} is loud
  F10  resume feeds the brief on stdin and keeps the launch flags
  F12  a missing envelope key refuses without discarding the payload
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import launch_support as ls

OWNER_IDENT = "xormania <127287135+xormania@users.noreply.github.com>"
OWNER_NAME = "xormania"
OWNER_EMAIL = "127287135+xormania@users.noreply.github.com"

DECOY_AUTHOR_NAME = "decoy-author"
DECOY_AUTHOR_EMAIL = "decoy-author@example.invalid"
DECOY_COMMITTER_NAME = "decoy-committer"
DECOY_COMMITTER_EMAIL = "decoy-committer@example.invalid"

ENVELOPE_KEYS = (
    "job", "status", "verdict", "counts", "findings",
    "artifacts", "spend", "stamp", "note",
)

EVIDENCE_NAMES = (
    "raw.out", "stderr", "exit", "state.json", "prompt.txt",
    "TRIPPED.md", "breaker.log",
)


def _add_dirs(argv, cwd):
    found = []
    argv = [str(a) for a in argv]
    i = 0
    while i < len(argv):
        a = argv[i]
        raw = None
        if a == "--add-dir" and i + 1 < len(argv):
            raw = argv[i + 1]
            i += 2
        elif a.startswith("--add-dir="):
            raw = a.split("=", 1)[1]
            i += 1
        else:
            i += 1
            continue
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = Path(cwd) / p
        found.append(p.resolve())
    return found


def _under(path, root):
    if path == root:
        return True
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _envelope_obj(job="plan", *, omit=(), extra=None):
    data = {
        "job": job,
        "status": "ok",
        "verdict": "approve",
        "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
        "findings": [],
        "artifacts": {},
        "spend": {"harness": "grok", "total": 0, "out": 0, "runs": 1},
        "stamp": {"ref": "harness-placeholder", "started": None, "ended": None},
        "note": None,
    }
    if extra:
        data.update(extra)
    for key in omit:
        data.pop(key, None)
    return data


class _ParserLaunch(ls._TempLaunch):
    """Helpers for the stdout-envelope parser."""

    def parse_envelope(self, raw, job="plan", sha="SHA", note=None):
        launch = self.load_launch()
        fn = getattr(launch, "_envelope", None)
        self.assertTrue(callable(fn), "launch._envelope parses stdout")
        got = fn(raw, job, sha) if note is None else fn(raw, job, sha, note)
        if isinstance(got, dict):
            return got
        if isinstance(got, tuple) and got and isinstance(got[0], dict):
            return got[0]
        self.fail(f"_envelope returned {type(got).__name__}: {got!r}")

    def envelope_of(self, rec):
        result = rec.get("result") or {}
        if isinstance(result, dict):
            env = result.get("envelope")
            if isinstance(env, dict):
                return env
        self.fail(f"record has no result.envelope: {rec!r}")


class ReadRoleThatCommitsIsInvalid(_ParserLaunch):
    """F1 / CONTRACT.md §Collect: Read roles: head must equal ref_sha.

    The existing ReadRoleHeadEqualsRef case never writes. A read role
    that commits must not close with envelope status intact.
    """

    def test_a_read_role_that_commits_closes_invalid_not_ok(self):
        os.environ["TASK_LAUNCH_COMMIT"] = "worker.py"
        os.environ["TASK_LAUNCH_HEAD_COMMIT"] = self.ref
        os.environ["TASK_LAUNCH_VERDICT"] = "approve"
        _code, _out, _err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
        ))
        rec = self.read_record()
        self.assertEqual(rec["role"], "read")
        result = rec["result"]
        ref_sha = rec["snapshot"]["ref_sha"]
        self.assertEqual(ref_sha, self.ref)
        self.assertNotEqual(
            result.get("head"), ref_sha,
            "plant: the read-role worker committed on top of the ref",
        )
        changed = ls.changed_entries(result.get("changed_paths"))
        self.assertTrue(
            any("worker.py" in str(item) for item in changed),
            f"plant: worker.py is in changed_paths, got {changed!r}",
        )
        envelope = self.envelope_of(rec)
        self.assertEqual(
            envelope.get("status"), "invalid",
            "CONTRACT.md §Collect: read roles, head must equal ref_sha; "
            "a commit in the snapshot is not recorded as an ordinary "
            f"ok/approve close: envelope={envelope!r}",
        )


class ClaudeAddDirDoesNotExposeJobBookkeeping(_ParserLaunch):
    """F3 / CONTRACT.md §The job directory.

    Bookkeeping (raw.out, exit, state.json, prompt.txt, …) is kept
    away from the harness tree. --add-dir may name in/ and out/; it
    must not name the job directory that holds the evidence files.
    """

    def test_claude_add_dir_does_not_include_job_directory_evidence_files(
            self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        argv = [str(a) for a in witness["argv"]]
        self.assertEqual(Path(argv[0]).name, "claude")
        job_dir = self.the_job_dir().resolve()
        self.assertTrue((job_dir / "prompt.txt").is_file(), "plant: prompt.txt")
        self.assertTrue((job_dir / "raw.out").is_file(), "plant: raw.out")
        self.assertTrue((job_dir / "exit").is_file(), "plant: exit")
        roots = _add_dirs(argv, witness["cwd"])
        self.assertNotIn(
            job_dir, roots,
            f"claude --add-dir must not grant the job directory "
            f"itself (evidence files live there): add-dir={roots!r}",
        )
        present = []
        for name in EVIDENCE_NAMES:
            path = (job_dir / name).resolve()
            if path.is_file():
                present.append(path)
        self.assertTrue(present, "plant: job-directory evidence files exist")
        leaked = []
        for path in present:
            for root in roots:
                if _under(path, root):
                    leaked.append((path.name, str(root)))
        self.assertEqual(
            leaked, [],
            "CONTRACT.md §The job directory: launcher evidence files "
            f"must sit outside every --add-dir, leaked={leaked!r} "
            f"add-dir={[str(r) for r in roots]} rec={rec['id']}",
        )


class SnapshotCommitIdentityIgnoresEnvOverrides(_ParserLaunch):
    """F4 / AGENTS.md §Attribution.

    GIT_AUTHOR_NAME and GIT_COMMITTER_* in the launcher's environment
    must not stamp the snapshot commit the launcher makes for a codex
    write job that left residual paths.
    """

    def _plant_decoy_identities(self):
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
            self.assertNotEqual(value, OWNER_NAME)
            self.assertNotEqual(value, OWNER_EMAIL)
        author = subprocess.run(
            ["git", "var", "GIT_AUTHOR_IDENT"],
            cwd=self.repo, capture_output=True, text=True,
        )
        committer = subprocess.run(
            ["git", "var", "GIT_COMMITTER_IDENT"],
            cwd=self.repo, capture_output=True, text=True,
        )
        self.assertEqual(author.returncode, 0, author.stderr)
        self.assertEqual(committer.returncode, 0, committer.stderr)
        self.assertIn(DECOY_AUTHOR_NAME, author.stdout)
        self.assertIn(DECOY_AUTHOR_EMAIL, author.stdout)
        self.assertIn(DECOY_COMMITTER_NAME, committer.stdout)
        self.assertIn(DECOY_COMMITTER_EMAIL, committer.stdout)
        self.assertNotIn(OWNER_NAME, author.stdout)
        self.assertNotIn(OWNER_EMAIL, author.stdout)

    def test_launcher_snapshot_commit_is_the_owner_despite_git_author_env(
            self):
        self._plant_decoy_identities()
        os.environ["TASK_LAUNCH_EDIT"] = "scratch.txt"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, *_ = self.launch_ok(self.argv_for(
            job="implement", harness="codex", stage="code",
            scope="python3 -m unittest",
        ))
        result = rec["result"]
        ref_sha = rec["snapshot"]["ref_sha"]
        head = result.get("head")
        self.assertTrue(head, "plant: launcher made a snapshot commit")
        self.assertNotEqual(
            head, ref_sha,
            "plant: a codex write with residual paths is committed",
        )
        snap = self.snapshot_of(rec)
        author = self._git(
            "log", "-1", "--format=%an <%ae>", head, repo=snap,
        ).stdout.strip()
        committer = self._git(
            "log", "-1", "--format=%cn <%ce>", head, repo=snap,
        ).stdout.strip()
        decoy_author = f"{DECOY_AUTHOR_NAME} <{DECOY_AUTHOR_EMAIL}>"
        decoy_committer = (
            f"{DECOY_COMMITTER_NAME} <{DECOY_COMMITTER_EMAIL}>"
        )
        self.assertEqual(author, OWNER_IDENT)
        self.assertEqual(committer, OWNER_IDENT)
        self.assertNotEqual(author, decoy_author)
        self.assertNotEqual(committer, decoy_committer)


class TrippedWriteIsNotCommittedByTheLauncher(_ParserLaunch):
    """F5 / a timeout or trip must not manufacture a snapshot commit."""

    def test_a_timed_out_codex_write_is_not_committed_by_the_launcher(self):
        os.environ["DISPATCH_TIMEOUT"] = "0.5"
        os.environ["TASK_LAUNCH_SLEEP"] = "8"
        os.environ["TASK_LAUNCH_EDIT"] = "scratch.txt"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        started = time.monotonic()
        _code, out, err = self.dispatch(self.argv_for(
            job="implement", harness="codex", stage="code",
            scope="python3 -m unittest",
        ))
        elapsed = time.monotonic() - started
        rec = self.read_record()
        text = (self.combined(out, err) + json.dumps(rec)).lower()
        self.assertLess(elapsed, 4, f"timeout must not wait out 8s: {elapsed}")
        self.assertIn("timeout", text)
        self.assertTrue(
            self.start_witness.is_file(),
            "plant: the harness child started before the timeout",
        )
        snap = self.snapshot_of(rec)
        scratch = snap / "scratch.txt"
        log_names = self._git(
            "log", "-1", "--name-only", "--format=", repo=snap,
        ).stdout
        self.assertTrue(
            scratch.is_file() or "scratch.txt" in log_names,
            "plant: the half-edit landed in the snapshot "
            f"(porcelain={self.porcelain(repo=snap)!r} log={log_names!r})",
        )
        result = rec["result"]
        ref_sha = rec["snapshot"]["ref_sha"]
        self.assertEqual(
            result.get("head"), ref_sha,
            "a timed-out codex write must not get a launcher-made "
            f"commit: head={result.get('head')!r} ref={ref_sha!r} "
            f"envelope={(result.get('envelope') or {})!r}",
        )
        dispatch_ref = f"refs/dispatch/{rec['id']}"
        refs = self.refs_map()
        if dispatch_ref in refs:
            self.assertEqual(
                refs[dispatch_ref], ref_sha,
                "refs/dispatch must not point at a manufactured commit "
                "from a timed-out run",
            )


class CloseCollectsTheHarnessStream(_ParserLaunch):
    """F6 / close ID must relocate and read the stream the way launch does."""

    def test_close_of_a_finished_uncollected_job_sets_model_ran_from_the_stream(
            self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        src_snap = Path(rec["snapshot"]["root"])
        self.assertTrue(src_snap.is_dir(), "plant: first snapshot exists")

        job_id = "20260826T000000Z-plan-grok-cl0se6"
        d = self.jobs_root / job_id
        d.mkdir()
        snap = d / "snapshot"
        self._git("clone", "--no-hardlinks", str(src_snap), str(snap))
        self.assertTrue((snap / ".git").exists() or (snap / ".git").is_file())

        session_id = "22222222-2222-4222-8222-222222222222"
        home = d / "home" / "grok"
        home.mkdir(parents=True)
        (home / "auth.json").write_text("{}\n", encoding="utf-8")
        stores = ls.load_path(self, ls.STORES_PATH, "task_launch_stores")
        stores.build_grok_store(
            home, str(snap.resolve()),
            base_timestamp=ls.STREAM_EPOCH,
            session_id=session_id,
            model=ls.RAN_MODEL,
            marker="CLOSE-COLLECT",
            head_commit=self.ref,
            git_root_dir=str(snap.resolve()),
            grok_home=str(home),
        )
        streams = list(home.rglob("summary.json"))
        self.assertEqual(len(streams), 1, "plant: one grok stream in job home")
        planted_model = json.loads(
            streams[0].read_text(encoding="utf-8"),
        ).get("current_model_id")
        self.assertEqual(planted_model, ls.RAN_MODEL)

        envelope = _envelope_obj(job="plan")
        (d / "raw.out").write_text(
            json.dumps(envelope) + "\n", encoding="utf-8",
        )
        (d / "exit").write_text("0\n", encoding="utf-8")
        (d / "prompt.txt").write_text("planted brief\n", encoding="utf-8")
        pid = self.dead_pid()
        (d / "state.json").write_text(json.dumps({
            "pid": pid, "pgid": pid,
            "session": {"id": session_id},
            "stream": None, "attempt": 1,
        }) + "\n", encoding="utf-8")
        self.assertTrue((d / "exit").is_file(), "plant: exit is present")
        self.assertTrue(streams[0].is_file(), "plant: stream sits on disk")

        seed = json.loads(json.dumps(rec))
        seed["id"] = job_id
        seed["status"] = "launched"
        seed["result"] = None
        seed["at"]["closed"] = None
        seed["model"]["ran"] = None
        seed["model"]["read_from"] = None
        seed["model"].pop("note", None)
        seed["session"] = {
            "id": session_id, "stream": None,
            "stream_sha256_at_close": None,
        }
        seed["snapshot"]["root"] = str(snap)
        seed["harness"]["name"] = "grok"
        iso = seed["harness"].setdefault("isolation", {})
        iso["home"] = str(home)
        iso["store"] = str(home / "sessions")
        iso["env"] = {"GROK_HOME": str(home), "HOME": str(home)}
        seed["harness"]["argv"] = [
            "grok", "-s", session_id, "--model", ls.REQUESTED_MODEL,
            "--output-format", "plain", "--permission-mode", "auto",
            "--prompt-file", str(d / "prompt.txt"),
        ]
        record_path = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        )
        record_path.write_text(
            json.dumps(seed, indent=2) + "\n", encoding="utf-8",
        )
        planted = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(planted["status"], "launched")
        self.assertIsNone(planted["result"])
        self.assertIsNone(planted["model"]["ran"])
        self.assertIsNone(planted["session"]["stream"])
        planted_store = Path(
            planted["harness"]["isolation"]["store"],
        ).resolve()
        self.assertEqual(planted_store, (home / "sessions").resolve())
        self.assertTrue(
            str(planted_store) in str(streams[0].resolve()),
            "plant: the stream sits under this job's isolation.store, "
            f"store={planted_store} stream={streams[0]}",
        )

        _code, out, err = self.run_main(["close", job_id])
        rec2 = json.loads(record_path.read_text(encoding="utf-8"))
        blob = (json.dumps(rec2) + self.combined(out, err)).lower()
        self.assertEqual(
            rec2["model"]["ran"], ls.RAN_MODEL,
            "close must read model.ran from the stream sitting in the "
            f"job home, not leave it null: rec={rec2!r} out={out!r} "
            f"err={err!r}",
        )
        stream = rec2.get("session", {}).get("stream")
        self.assertTrue(
            stream,
            f"close must set session.stream: rec={rec2!r}",
        )
        self.assertTrue(
            Path(stream).is_file(),
            f"session.stream must name a file that exists: {stream!r}",
        )
        self.assertTrue(
            rec2.get("session", {}).get("stream_sha256_at_close"),
            "close must set stream_sha256_at_close",
        )
        self.assertNotIn("no stream was found", blob)


class TrailingNarrationAfterPrettyEnvelopeIsTolerated(_ParserLaunch):
    """F7 / narration after a pretty-printed object is tolerated."""

    def test_narration_after_a_pretty_printed_envelope_does_not_drop_it(self):
        pretty = json.dumps(_envelope_obj(job="plan"), indent=2)
        self.assertIn("\n", pretty, "plant: the object spans several lines")
        trailing = "tokens used: 12345"
        raw = (pretty + "\n" + trailing + "\n").encode("utf-8")
        self.assertTrue(
            raw.strip().endswith(trailing.encode("utf-8")),
            "plant: narration sits after the closing brace",
        )
        self.assertIn(b"\n}", raw)
        got = self.parse_envelope(raw, "plan", "SHA")
        note = str(got.get("note") or "")
        self.assertNotEqual(
            got.get("status"), "invalid",
            "narration after a pretty-printed envelope must not be "
            f"recorded as unparseable: {got!r}",
        )
        self.assertNotIn("envelope-parse", note)
        self.assertEqual(got.get("job"), "plan")
        self.assertEqual(got.get("status"), "ok")


class RuntimeNoteDoesNotDestroyParseNote(_ParserLaunch):
    """F8 / a runtime note appends; it does not replace a parse diagnostic."""

    def test_a_runtime_note_keeps_the_envelope_parse_reason(self):
        raw = b"not json\n"
        self.assertFalse(raw.lstrip().startswith(b"{"), "plant: not an object")
        got = self.parse_envelope(
            raw, "plan", "SHA", "head_commit mismatch",
        )
        note = str(got.get("note") or "")
        self.assertIn(
            "envelope-parse", note,
            "the parse reason must survive a supplied runtime note: "
            f"note={note!r} envelope={got!r}",
        )
        self.assertIn("head_commit mismatch", note)
        self.assertNotEqual(
            note.strip(), "head_commit mismatch",
            "overwriting the parse diagnostic with only the runtime "
            f"note loses why the envelope itself was lost: {note!r}",
        )


class ReviewDiffUsesTheMostRecentAnchor(_ParserLaunch):
    """F9 / review base is the most recent resolvable merge-base.

    origin/dev at root is stale; local dev at mid is the recent fork
    point. A zero-byte {diff} on a job whose template names {{diff}}
    is a silent failure, not a success.
    """

    def test_stale_origin_dev_is_not_preferred_over_local_dev(self):
        self._git("branch", "dev", self.mid_sha)
        self._git("update-ref", "refs/remotes/origin/dev", self.root_sha)
        self.assertEqual(
            self._git("rev-parse", "dev").stdout.strip(), self.mid_sha,
        )
        self.assertEqual(
            self._git("rev-parse", "refs/remotes/origin/dev").stdout.strip(),
            self.root_sha,
        )
        self.assertNotEqual(self.mid_sha, self.root_sha)
        self.assertNotEqual(self.mid_sha, self.ref)
        self.assertNotEqual(self.root_sha, self.ref)

        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="adversarial-review", harness="grok", stage="review",
            scope="pin the review merge-base",
        ))
        prompt = witness.get("prompt_text") or ""
        if not prompt:
            prompt = (self.the_job_dir() / "prompt.txt").read_text(
                encoding="utf-8",
            )
        self.assertIn(self.ref, prompt, "{ref} lands in the brief")
        self.assertIn(
            self.mid_sha, prompt,
            "the most recent resolvable merge-base is local dev "
            f"(mid={self.mid_sha}); prompt={prompt!r}",
        )
        self.assertNotIn(
            self.root_sha, prompt,
            "stale origin/dev (root) must not win over local dev: "
            f"prompt={prompt!r}",
        )
        job_dir = self.the_job_dir()
        diff_path = job_dir / "diff.patch"
        self.assertTrue(
            diff_path.is_file(),
            f"{{diff}} is written to the job directory: {list(job_dir.iterdir())}",
        )
        body = diff_path.read_text(encoding="utf-8")
        self.assertTrue(
            body.strip(),
            "a job whose template names {diff} must not proceed silently "
            f"with a zero-byte diff.patch (base==ref would be empty); "
            f"rec base={rec.get('lineage')!r} ref={self.ref}",
        )
        self.assertIn("alpha v2", body)
        self.assertIn("alpha v3", body)
        self.assertNotIn(
            "alpha v1", body,
            "diff against stale origin/dev (root) includes alpha v1; "
            "the recent fork point is mid (v2→v3)",
        )


class ResumeFeedsTheBriefAndKeepsFlags(_ParserLaunch):
    """F10 / resume feeds the brief on stdin and carries the launch flags.

    Look up each dispatch by id. After a second launch both records
    remain at .dev/records/dispatches/<id>.json (CONTRACT.md §The
    record); the_job_dir / the_record_path "exactly one" helpers
    must not be used here — they pinned the archive behaviour G2
    now forbids.
    """

    def test_claude_and_codex_resume_feed_the_brief_on_stdin(self):
        rec, first, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        id_a = rec["id"]
        record_a = (
            self.repo / ".dev" / "records" / "dispatches" / f"{id_a}.json"
        )
        job_a = self.jobs_root / id_a
        self.assertTrue(
            record_a.is_file(),
            f"plant: first record is at the canonical path {record_a}",
        )
        self.assertTrue(
            job_a.is_dir(),
            f"plant: first job directory is under jobs-root {job_a}",
        )
        brief = (job_a / "prompt.txt").read_text(encoding="utf-8")
        self.assertTrue(brief, "plant: prompt.txt holds the rendered brief")
        self.assertEqual(first.get("stdin"), brief)
        first_argv = [str(a) for a in first["argv"]]
        self.assertIn("--add-dir", first_argv)
        self.assertIn("--model", first_argv)

        self.witness.unlink()
        code, out, err = self.run_main(["resume", id_a])
        self.assertEqual(code, 0, self.combined(out, err))
        second = self.read_witness()
        argv = [str(a) for a in second["argv"]]
        self.assertEqual(Path(argv[0]).name, "claude")
        self.assertEqual(
            second.get("stdin"), brief,
            "claude resume must feed prompt.txt on stdin; "
            f"got {second.get('stdin')!r}",
        )
        self.assertTrue(second.get("stdin"), "resume stdin is empty")
        self.assertIn("--add-dir", argv)
        self.assertIn("--model", argv)
        self.assertEqual(
            argv[argv.index("--model") + 1], ls.REQUESTED_MODEL,
        )
        self.assertTrue(
            record_a.is_file(),
            "resume itself must not move the record off the canonical path",
        )

        self.witness.unlink()
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan",
        ))
        self.assertEqual(
            code, 0,
            f"second launch must succeed: {self.combined(out, err)}",
        )
        self.assertTrue(
            record_a.is_file(),
            "CONTRACT.md §The record: one file per dispatch at "
            f".dev/records/dispatches/{id_a}.json; a later launch "
            "must not move a closed, resumed job's record off the "
            "canonical path",
        )
        self.assertTrue(
            job_a.is_dir(),
            f"job directory must stay at {job_a}",
        )
        records = self.record_files()
        names = [p.name for p in records]
        self.assertEqual(
            len(records), 2,
            "CONTRACT.md §The record: one file per dispatch, both "
            "stay at the canonical top-level path after a second "
            f"launch; got {names!r}",
        )
        self.assertIn(f"{id_a}.json", names)
        new_files = [p for p in records if p.stem != id_a]
        self.assertEqual(
            len(new_files), 1,
            f"the second launch adds one canonical record, got {names!r}",
        )
        rec_x = self.read_record(new_files[0])
        self.assertEqual(rec_x.get("status"), "closed")
        id_x = rec_x["id"]
        self.assertNotEqual(id_x, id_a)
        job_x = self.jobs_root / id_x
        self.assertTrue(
            job_x.is_dir(),
            f"plant: second job directory is at {job_x}",
        )
        first_x = self.read_witness()
        brief_x = (job_x / "prompt.txt").read_text(encoding="utf-8")
        self.assertTrue(brief_x, "plant: codex prompt.txt")
        self.assertEqual(first_x.get("stdin"), brief_x)
        self.witness.unlink()
        code, out, err = self.run_main(["resume", id_x])
        self.assertEqual(code, 0, self.combined(out, err))
        second_x = self.read_witness()
        self.assertEqual(Path(second_x["argv"][0]).name, "codex")
        self.assertEqual(
            second_x.get("stdin"), brief_x,
            "codex resume must feed prompt.txt on stdin; "
            f"got {second_x.get('stdin')!r}",
        )
        self.assertTrue(
            record_a.is_file(),
            "the first record stays at its canonical path after the "
            "codex resume",
        )
        self.assertTrue(
            new_files[0].is_file(),
            "the second record stays at its canonical path after resume",
        )
        self.assertEqual(
            len(self.record_files()), 2,
            "both records remain top-level after the second resume",
        )


class MissingEnvelopeKeyKeepsThePayload(_ParserLaunch):
    """F12 / a missing key refuses without discarding the parsed payload."""

    def test_a_missing_spend_key_keeps_findings_counts_and_verdict(self):
        extra = {
            "job": "adversarial-review",
            "status": "ok",
            "verdict": "changes",
            "counts": {"p1": 1, "p2": 0, "p3": 0, "opinions": 0},
            "findings": [{
                "id": "X1", "severity": "p1", "file": "a.py",
                "finding": "kept",
            }],
            "artifacts": {"report": "out/R.md"},
            "note": None,
        }
        payload = _envelope_obj(job="adversarial-review", omit=("spend",),
                                extra=extra)
        self.assertNotIn("spend", payload)
        self.assertEqual(len(payload["findings"]), 1)
        self.assertEqual(payload["verdict"], "changes")
        for key in ENVELOPE_KEYS:
            if key == "spend":
                self.assertNotIn(key, payload)
            else:
                self.assertIn(key, payload)
        raw = json.dumps(payload).encode("utf-8")
        got = self.parse_envelope(raw, "adversarial-review", "SHA")
        note = str(got.get("note") or "")
        self.assertEqual(
            got.get("status"), "invalid",
            "a missing key is a refusal, not a default: "
            f"envelope={got!r}",
        )
        self.assertIn("envelope-missing", note)
        self.assertIn("spend", note)
        self.assertEqual(
            got.get("findings"), payload["findings"],
            "the parsed findings must survive a missing-key refusal: "
            f"envelope={got!r}",
        )
        self.assertEqual(got.get("verdict"), "changes")
        self.assertEqual(got.get("counts"), payload["counts"])
        self.assertEqual(got.get("artifacts"), payload["artifacts"])
        self.assertEqual(got.get("job"), "adversarial-review")
