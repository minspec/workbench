"""Isolation, child env, stream discovery, session ids.

Written from CONTRACT.md §Dispatch Isolation, child's environment,
Watching. Plan items (g)(h)(j)(k)(t)(u).

  I1  claude flags; HOME untouched; CLAUDE_CONFIG_DIR unset
  I2  CODEX_HOME / GROK_HOME+HOME point at a home holding exactly auth.json
  I3  agent-env: CLICOLOR_FORCE absent, NO_COLOR set, WF_LANE, DISPATCH_JOB
  I4  stream chosen under the isolated store, not a newer ~/.codex
  I5  unsupervised: live, no stream within grace → terminated, invalid
  I6  minted session id on argv; codex from session_meta
  I7  a fake that ignores --session-id is a recorded mismatch
  I8  observed is unresolved or verbatim, never false
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import launch_support as ls


class ClaudeIsolationIsFlagsAndHomeUntouched(ls._TempLaunch):
    """I1 / contract isolation table / plan (k)."""

    def test_claude_argv_carries_the_isolation_flags_and_session_id(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        argv = [str(p) for p in witness["argv"]]
        self.assertIn("--setting-sources", argv)
        self.assertEqual(
            argv[argv.index("--setting-sources") + 1], "project,local",
        )
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--session-id", argv)
        minted = argv[argv.index("--session-id") + 1]
        self.assertTrue(minted, "a session id is minted at launch")
        self.assertEqual(rec["session"]["id"], minted)
        self.assertIn("--print", argv)
        self.assertIn("--permission-mode", argv)
        # acceptEdits, not plan: a plan job writes out/PLAN.md and plan mode
        # ends the turn at a plan (2026-08-28). out/ is the only added dir.
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("--add-dir", argv)
        self.assertTrue(argv[argv.index("--add-dir") + 1].endswith("/out"))
        env = witness["env"]
        self.assertEqual(env.get("HOME"), str(self.home))
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(rec["harness"]["isolation"]["mechanism"], "flags")
        self.assertEqual(
            os.path.realpath(witness["cwd"]),
            os.path.realpath(self.snapshot_of(rec)),
        )


class MinimalHomesHoldExactlyAuthJson(ls._TempLaunch):
    """I2 / plan (k)."""

    def test_codex_home_is_the_job_home_holding_exactly_auth_json(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="codex", stage="plan",
        )
        env = witness["env"]
        home = env.get("CODEX_HOME")
        self.assertTrue(home, "CODEX_HOME must be set")
        job_dir = self.the_job_dir()
        self.assertEqual(
            os.path.realpath(home),
            os.path.realpath(job_dir / "home" / "codex"),
        )
        names = sorted(p.name for p in Path(home).iterdir())
        self.assertEqual(names, ["auth.json"])
        auth = Path(home) / "auth.json"
        self.assertTrue(auth.is_symlink() or auth.is_file())
        self.assertNotEqual(env.get("HOME"), home)
        self.assertEqual(rec["harness"]["containment"], "os")
        argv = [str(p) for p in witness["argv"]]
        self.assertIn("--sandbox", argv)
        # workspace-write for read roles too: read-only denied the job its
        # own out/ deliverable and a writable tempdir for the suite it must
        # run (2026-08-28, check-tests: "no affected assertion ran"). The
        # snapshot's integrity is proved after the run instead.
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        self.assertIn("-c", argv)
        self.assertTrue(any(a.startswith("sandbox_workspace_write.writable_roots=") and a.endswith('/out"]') for a in argv))

    def test_grok_home_and_home_both_point_at_the_job_home(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        env = witness["env"]
        grok_home = env.get("GROK_HOME")
        home = env.get("HOME")
        self.assertTrue(grok_home and home)
        job_dir = self.the_job_dir()
        expected = os.path.realpath(job_dir / "home" / "grok")
        self.assertEqual(os.path.realpath(grok_home), expected)
        self.assertEqual(os.path.realpath(home), expected)
        names = sorted(p.name for p in Path(grok_home).iterdir())
        self.assertEqual(names, ["auth.json"])
        self.assertNotEqual(
            os.path.realpath(home), os.path.realpath(self.home),
            "operator HOME must not leak into the grok child",
        )
        self.assertEqual(rec["harness"]["containment"], "policy")


class AgentEnvIsAppliedToTheChild(ls._TempLaunch):
    """I3 / contract child's environment."""

    def test_clicolor_force_is_absent_and_no_color_is_set(self):
        os.environ["CLICOLOR_FORCE"] = "1"
        os.environ["FORCE_COLOR"] = "1"
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        env = witness["env"]
        self.assertNotIn("CLICOLOR_FORCE", env)
        self.assertNotIn("FORCE_COLOR", env)
        self.assertEqual(env.get("NO_COLOR"), "1")
        self.assertEqual(env.get("CLICOLOR"), "0")
        self.assertEqual(env.get("TERM"), "dumb")
        self.assertEqual(env.get("PAGER"), "cat")
        self.assertEqual(env.get("GH_PAGER"), "cat")
        self.assertEqual(env.get("GIT_PAGER"), "cat")
        self.assertEqual(env.get("CI"), "true")
        self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")
        self.assertEqual(env.get("GIT_EDITOR"), "true")
        self.assertEqual(env.get("EDITOR"), "true")
        self.assertEqual(env.get("PYTHONUNBUFFERED"), "1")
        self.assertEqual(env.get("PYTHONIOENCODING"), "utf-8")
        self.assertEqual(env.get("LC_ALL"), "C.UTF-8")
        self.assertEqual(env.get("WF_LANE"), "dev")
        self.assertEqual(env.get("DISPATCH_JOB"), rec["id"])
        self.assertEqual(env.get("LESS"), "FRX")


class StreamIsFoundUnderTheIsolatedStore(ls._TempLaunch):
    """I4 / plan (j) — a newer stream planted in ~/.codex is not chosen."""

    def test_a_newer_operator_codex_stream_is_not_chosen(self):
        stores = ls.load_path(self, ls.STORES_PATH, "task_launch_stores")
        operator_root = self.home / ".codex"
        decoy_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        stores.build_codex_store(
            operator_root,
            base_timestamp=ls.STREAM_EPOCH + 50_000,
            cwd="/decoy/cwd",
            session_id=decoy_id,
            model="decoy-operator-model",
            effort="high",
            marker="DECOY",
        )
        rollouts = list(operator_root.glob("sessions/*/*/*/rollout-*.jsonl"))
        self.assertEqual(len(rollouts), 1, "plant: exactly one decoy stream")
        decoy = rollouts[0]
        # Newer mtime than anything the fake will write during launch.
        os.utime(decoy, (2_000_000_000, 2_000_000_000))
        self.assertGreater(decoy.stat().st_mtime, 1_900_000_000)
        self.assertIn(b"decoy-operator-model", decoy.read_bytes())

        rec, witness, *_ = self.launch_ok(
            job="plan", harness="codex", stage="plan",
        )
        self.assertEqual(rec["model"]["ran"], ls.RAN_MODEL)
        self.assertNotEqual(rec["model"]["ran"], "decoy-operator-model")
        stream = rec["session"]["stream"] or ""
        self.assertTrue(stream, "a stream path is recorded")
        self.assertNotIn(str(decoy), stream)
        isolated_home = witness["env"].get("CODEX_HOME")
        self.assertTrue(isolated_home)
        self.assertIn(os.path.realpath(isolated_home), os.path.realpath(stream))
        self.assertNotIn(
            os.path.realpath(operator_root / "sessions"),
            os.path.realpath(stream),
        )
        self.assertNotEqual(rec["model"]["ran"], "decoy-operator-model")
        self.assertNotIn(
            "TASK_LAUNCH_RAN_MODEL", os.environ,
            "ran must be parsed from the stream, not copied from the env",
        )

    def test_a_newer_operator_claude_stream_is_not_chosen(self):
        stores = ls.load_path(self, ls.STORES_PATH, "task_launch_stores")
        decoy_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        decoy_root = self.home / ".claude" / "projects"
        stores.build_claude_store(
            decoy_root, "decoy-slug",
            base_timestamp=ls.STREAM_EPOCH + 50_000,
            cwd="/decoy/cwd", session_id=decoy_id,
            model="decoy-claude-model", effort="high",
            marker="DECOY-CLAUDE",
        )
        decoys = list(decoy_root.glob("decoy-slug/*.jsonl"))
        self.assertEqual(len(decoys), 1, "plant: one decoy claude stream")
        os.utime(decoys[0], (2_000_000_000, 2_000_000_000))
        self.assertIn(b"decoy-claude-model", decoys[0].read_bytes())
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        self.assertEqual(rec["model"]["ran"], ls.RAN_MODEL)
        self.assertNotEqual(rec["model"]["ran"], "decoy-claude-model")
        stream = rec["session"]["stream"] or ""
        self.assertTrue(stream)
        self.assertNotIn("decoy-slug", stream)
        self.assertNotIn(decoy_id, stream)

    def test_a_newer_operator_grok_stream_is_not_chosen(self):
        stores = ls.load_path(self, ls.STORES_PATH, "task_launch_stores")
        decoy_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        operator = self.home / ".grok"
        stores.build_grok_store(
            operator, "/decoy/cwd",
            base_timestamp=ls.STREAM_EPOCH + 50_000,
            session_id=decoy_id, model="decoy-grok-model",
            marker="DECOY-GROK",
        )
        decoys = list(operator.glob("sessions/*/*/summary.json"))
        self.assertGreaterEqual(len(decoys), 1, "plant: grok decoy stream")
        self.assertIn(b"decoy-grok-model", decoys[0].read_bytes())
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        self.assertEqual(rec["model"]["ran"], ls.RAN_MODEL)
        self.assertNotEqual(rec["model"]["ran"], "decoy-grok-model")
        stream = rec["session"]["stream"] or ""
        isolated = witness["env"].get("GROK_HOME")
        self.assertTrue(isolated)
        self.assertIn(os.path.realpath(isolated), os.path.realpath(stream))
        self.assertNotIn(decoy_id, stream)


class UnsupervisedLiveProcessIsTerminated(ls._TempLaunch):
    """I5 / contract unsupervised / plan (j).

    Seam: ``DISPATCH_STREAM_GRACE`` (default 120) and injected
    ``launch.monotonic`` / ``launch.sleep``. The contract pins 120-second
    behaviour, not a Python attribute named STREAM_GRACE.
    """

    def _invalid_envelope(self, rec, text):
        envelope = rec.get("result") or {}
        if isinstance(envelope, dict):
            envelope = envelope.get("envelope") or envelope
        status = (envelope or {}).get("status") or rec.get("status")
        self.assertTrue(
            status == "invalid"
            or (isinstance(envelope, dict)
                and envelope.get("status") == "invalid"),
            f"unsupervised run must surface invalid: rec={rec!r} text={text!r}",
        )
        self.assertTrue(
            any(w in text.lower()
                for w in ("unsupervised", "no stream", "invalid", "store")),
            f"must name the store searched: {text!r}",
        )
        self.assertIsNone(rec["model"]["ran"])

    def test_no_stream_within_grace_kills_the_process_group(self):
        self.set_grace(0.3)
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "0"
        os.environ["TASK_LAUNCH_SLEEP"] = "8"
        os.environ["TASK_LAUNCH_GRANDCHILD"] = str(self.grandchild)

        def _reap():
            if self.start_witness.is_file():
                info = json.loads(
                    self.start_witness.read_text(encoding="utf-8")
                )
                ls.kill_if_alive(info.get("pid"))
                ls.kill_if_alive(info.get("pgid"))
            if self.grandchild.is_file():
                gpid = self.grandchild.read_text(encoding="utf-8").strip()
                ls.kill_if_alive(gpid)

        self.addCleanup(_reap)
        started = time.monotonic()
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan",
        ))
        elapsed = time.monotonic() - started
        rec = self.read_record()
        text = self.combined(out, err) + json.dumps(rec)
        self.assertNotEqual(code, 0)
        self._invalid_envelope(rec, text)
        self.assertGreaterEqual(elapsed, 0.25, "grace must actually wait")
        self.assertLess(elapsed, 4, "must not wait out the 8s child")
        started_info = self.read_start_witness()
        pid = started_info["pid"]
        pgid = started_info["pgid"]
        self.assertFalse(
            ls.pid_is_alive(pid),
            f"harness pid {pid} was left running after unsupervised kill",
        )
        self.assertFalse(
            ls.pid_is_alive(pgid),
            f"process group {pgid} was left running",
        )
        if self.grandchild.is_file():
            gpid = int(self.grandchild.read_text(encoding="utf-8").strip())
            self.assertFalse(
                ls.pid_is_alive(gpid),
                f"grandchild {gpid} survived the group kill",
            )

    def test_default_grace_is_one_hundred_and_twenty_seconds_on_the_clock(
            self):
        os.environ.pop("DISPATCH_STREAM_GRACE", None)
        clock = self.attach_clock()
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "0"
        os.environ["TASK_LAUNCH_SLEEP"] = "30"
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="codex", stage="plan",
        ))
        rec = self.read_record()
        text = self.combined(out, err) + json.dumps(rec)
        self.assertNotEqual(code, 0)
        self._invalid_envelope(rec, text)
        waited = clock.now if clock.sleeps else sum(clock.sleeps)
        self.assertGreaterEqual(
            max(clock.now, sum(clock.sleeps), waited),
            ls.DEFAULT_GRACE,
            f"default grace is 120s; clock advanced {clock.now} "
            f"sleeps={clock.sleeps}",
        )
        if self.start_witness.is_file():
            pid = self.read_start_witness()["pid"]
            self.assertFalse(ls.pid_is_alive(pid))


class SessionIdIsMintedOrRead(ls._TempLaunch):
    """I6 / plan (h)."""

    def test_grok_receives_dash_s_uuid(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        argv = [str(p) for p in witness["argv"]]
        self.assertIn("-s", argv)
        minted = argv[argv.index("-s") + 1]
        self.assertEqual(rec["session"]["id"], minted)
        self.assertRegex(
            minted,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        )

    def test_codex_session_id_is_read_from_session_meta(self):
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        stream = rec["session"]["stream"]
        self.assertTrue(stream)
        body = Path(stream).read_text(encoding="utf-8")
        self.assertIn(rec["session"]["id"], body)
        self.assertIn("session_meta", body)


class IgnoringTheMintedIdIsAMismatch(ls._TempLaunch):
    """I7 / plan (t)."""

    def test_a_fake_that_ignores_session_id_is_recorded_as_a_mismatch(self):
        os.environ["TASK_LAUNCH_IGNORE_SESSION"] = "1"
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        argv = [str(p) for p in witness["argv"]]
        self.assertIn("--session-id", argv)
        minted = argv[argv.index("--session-id") + 1]
        ignored = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        self.assertNotEqual(minted, ignored)
        blob = json.dumps(rec).lower()
        self.assertIn("mismatch", blob)
        self.assertIn(minted, json.dumps(rec))
        self.assertIn(ignored, json.dumps(rec))
        self.assertNotEqual(rec["session"]["id"], ignored)


class ObservedIsUnresolvedOrVerbatim(ls._TempLaunch):
    """I8 / plan (u). Tests do not run a live behavioural probe."""

    def test_observed_is_unresolved_or_a_probe_dict_never_a_bare_false(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        observed = rec["harness"]["isolation"]["observed"]
        self.assertIsInstance(observed, dict)
        self.assertNotEqual(observed, False)
        self.assertNotEqual(observed, True)
        if "unresolved" in observed:
            self.assertTrue(str(observed["unresolved"]).strip())
        else:
            self.assertIn("evidence", observed)
            self.assertIn("checked_at", observed)
            self.assertIn("harness_version", observed)
            self.assertIn("operator_config_present", observed)
            # A real probe may report False. A bare `observed: false`
            # (the manufactured shape) is already refused above.


class CodexReadsTheBriefOnStdin(ls._TempLaunch):
    """codex exec with DEVNULL on stdin exits 1 "No prompt provided via
    stdin" before any work. Three dispatches on 2026-08-28 closed
    harness-cli: exited 1 with empty findings and were committed as records."""

    def test_codex_argv_ends_in_dash_and_stdin_carries_the_brief(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="codex", stage="plan",
        )
        argv = [str(p) for p in witness["argv"]]
        self.assertEqual(Path(argv[0]).name, "codex")
        self.assertEqual(argv[1], "exec")
        self.assertEqual(argv[-1], "-")
        brief = (Path(rec["snapshot"]["root"]).parent / "prompt.txt").read_text(
            encoding="utf-8",
        )
        self.assertTrue(brief, "the rendered brief is empty")
        self.assertEqual(witness["stdin"], brief)


class ClaudeReadsTheBriefOnStdin(ls._TempLaunch):
    """claude --print with DEVNULL on stdin exits 1 "Input must be provided
    either through stdin or as a prompt argument" (dispatch
    20260828T204050Z-plan-claude-61a0f6, harness-cli: exited 1)."""

    def test_claude_stdin_carries_the_brief(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        brief = (Path(rec["snapshot"]["root"]).parent / "prompt.txt").read_text(
            encoding="utf-8",
        )
        self.assertTrue(brief, "the rendered brief is empty")
        self.assertEqual(witness["stdin"], brief)


class GrokArgvCarriesTheSandboxTheRecordStates(ls._TempLaunch):
    """A record saying sandbox: plan over an argv with no --permission-mode
    claims a permission mode the child never had (dispatch
    20260828T194753Z-review-grok-6a8e2c)."""

    def test_grok_argv_carries_the_sandbox_the_record_states_and_plain_output(self):
        # The record states `always-approve`; grok's flag for it is
        # `--always-approve`, not a --permission-mode value. Under `auto` a
        # session raised 111 prompts and the last timed out after 30 s on a
        # non-interactive stdin (2026-08-29, tests-grok-abfc3b).
        # U10: grok --json-schema implies json output; plain was the
        # scan-fallback argv and contradicts structured envelopes.
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        argv = [str(p) for p in witness["argv"]]
        self.assertEqual(rec["harness"]["sandbox"], "always-approve")
        self.assertIn("--always-approve", argv)
        self.assertNotIn("--permission-mode", argv)
        self.assertNotIn("--json-schema", argv)  # grok 1.0.5 short-circuits under --json-schema (record 66ccb2)
        if "--output-format" in argv:
            self.assertEqual(
                argv[argv.index("--output-format") + 1], "plain",
                f"U10: grok output is plain, not json; argv={argv!r}",
            )
        self.assertIn("--prompt-file", argv)
