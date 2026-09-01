"""Regression contracts for the six pulse findings from PR #24."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

HERE = Path(__file__).resolve()
PULSE = HERE.parents[1] / "pulse.py"

BASE_EPOCH = 1_787_306_400
NOW = BASE_EPOCH + 600
BASE_ISO = "2026-08-21T10:00:00.000Z"
LATER_ISO = "2026-08-21T10:00:01.000Z"
REPO = "/home/work/projects/minspec/workbench"


def load_module(testcase, path, name):
    testcase.assertTrue(path.is_file(), f"required module is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    testcase.assertIsNotNone(spec, f"could not create an import spec for {path}")
    testcase.assertIsNotNone(spec.loader, f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n"
            for entry in entries
        )
    )
    return path


class PulseFindings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pulse-findings-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.empty_claude = self.root / "empty-claude"
        self.empty_codex = self.root / "empty-codex"
        self.empty_grok = self.root / "empty-grok"
        for path in (self.empty_claude, self.empty_codex, self.empty_grok):
            path.mkdir()

        import_time_home = self.root / "import-time-codex-home"
        import_time_home.mkdir()
        with patch.dict(
            os.environ,
            {"CODEX_HOME": str(import_time_home)},
            clear=False,
        ):
            self.pulse = load_module(
                self,
                PULSE,
                f"minspec_pulse_findings_{self._testMethodName}",
            )

    def set_mtime(self, path, timestamp):
        os.utime(path, (timestamp, timestamp))
        self.assertAlmostEqual(
            path.stat().st_mtime,
            timestamp,
            places=3,
            msg=f"the activity mtime plant did not land on {path}",
        )

    def build_claude(self, root, repo, session, activity="Read", idle=10.0):
        slug = "-" + "-".join(repo.strip("/").split("/"))
        stream = write_jsonl(
            root / slug / f"{session}.jsonl",
            [
                {
                    "timestamp": BASE_ISO,
                    "cwd": repo,
                    "sessionId": session,
                    "message": {
                        "id": f"message-{session}",
                        "model": "claude-fable-5",
                        "usage": {
                            "input_tokens": 1,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 1,
                        },
                        "content": [
                            {
                                "type": "tool_use",
                                "name": activity,
                                "input": {},
                            }
                        ],
                    },
                }
            ],
        )
        self.set_mtime(stream, NOW - idle)
        return stream

    def build_codex(
        self,
        root,
        repo,
        session,
        model="gpt-5-codex",
        activity=(),
        idle=10.0,
    ):
        entries = [
            {
                "timestamp": BASE_ISO,
                "type": "session_meta",
                "payload": {"id": session, "cwd": repo},
            },
            {
                "timestamp": LATER_ISO,
                "type": "turn_context",
                "payload": {"cwd": repo, "model": model},
            },
        ]
        for index, payload in enumerate(activity, start=2):
            entries.append(
                {
                    "timestamp": f"2026-08-21T10:00:{index:02d}.000Z",
                    "type": "response_item",
                    "payload": payload,
                }
            )
        stream = write_jsonl(
            root
            / "sessions"
            / "2026"
            / "08"
            / "21"
            / f"rollout-2026-08-21T10-00-00-{session}.jsonl",
            entries,
        )
        self.set_mtime(stream, NOW - idle)
        return stream

    def build_grok(
        self,
        root,
        repo,
        session,
        activity="grok_tool",
        idle=10.0,
    ):
        session_dir = root / "sessions" / quote(repo, safe="") / session
        session_dir.mkdir(parents=True, exist_ok=True)
        summary = session_dir / "summary.json"
        summary.write_text(
            json.dumps(
                {
                    "info": {"id": session, "cwd": repo},
                    "current_model_id": "grok-4.6",
                    "created_at": BASE_ISO,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        events = write_jsonl(
            session_dir / "events.jsonl",
            [
                {
                    "timestamp": LATER_ISO,
                    "name": activity,
                    "ts": LATER_ISO,
                    "type": "tool_started",
                    "tool_name": activity,
                }
            ],
        )
        self.set_mtime(events, NOW - idle)
        return events

    def invoke(self, *args, env=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = contextlib.nullcontext()
        if env is not None:
            environment = patch.dict(os.environ, env, clear=False)

        with (
            environment,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            returncode = self.pulse.main(list(args))

        self.assertEqual(
            returncode,
            0,
            f"{PULSE} returned {returncode}: {stderr.getvalue()}",
        )
        try:
            document = json.loads(stdout.getvalue())
        except json.JSONDecodeError as error:
            self.fail(f"{PULSE} did not emit one JSON object: {error}")
        self.assertEqual(
            set(document),
            {"sessions"},
            f"{PULSE} emitted an unexpected JSON document",
        )
        self.assertIsInstance(document["sessions"], list)
        return document

    def json_args(self, *, repo=REPO, claude=None, codex=None, grok=None, tail=10):
        args = [
            "--json",
            "--repo",
            repo,
            "--now",
            str(NOW),
            "--live-window",
            "300",
            "--tail",
            str(tail),
            "--claude-dir",
            str(claude or self.empty_claude),
            "--grok-dir",
            str(grok or self.empty_grok),
        ]
        if codex is not None:
            args.extend(("--codex-dir", str(codex)))
        return args

    def test_b1_codex_home_is_dynamic_and_explicit_dir_wins(self):
        first_home = self.root / "codex-home-first"
        second_home = self.root / "codex-home-second"
        first_id = "11111111-1111-4111-8111-111111111111"
        second_id = "22222222-2222-4222-8222-222222222222"
        first_model = "gpt-5-codex-first-home"
        second_model = "gpt-5-codex-second-home"
        self.build_codex(first_home, REPO, first_id, model=first_model)
        self.build_codex(second_home, REPO, second_id, model=second_model)

        first = self.invoke(
            *self.json_args(),
            env={"CODEX_HOME": str(first_home)},
        )
        second = self.invoke(
            *self.json_args(),
            env={"CODEX_HOME": str(second_home)},
        )
        explicit = self.invoke(
            *self.json_args(codex=second_home),
            env={"CODEX_HOME": str(first_home)},
        )

        observed = tuple(
            [row["model"] for row in document["sessions"]]
            for document in (first, second, explicit)
        )
        self.assertEqual(
            observed,
            ([first_model], [second_model], [second_model]),
            (
                "CODEX_HOME must be read for every main() invocation, "
                "while --codex-dir must take precedence"
            ),
        )

    def test_b2_codex_metadata_merges_across_meta_and_context_records(self):
        codex_root = self.root / "codex-b2"
        session = "33333333-3333-4333-8333-333333333333"
        model = "gpt-5.6-codex"
        self.build_codex(codex_root, REPO, session, model=model)

        document = self.invoke(*self.json_args(codex=codex_root))
        rows = document["sessions"]
        self.assertEqual(
            rows and len(rows),
            1,
            "the live Codex rollout was not reported",
        )
        self.assertEqual(
            (rows[0]["session"], rows[0]["model"]),
            (session, model),
            "later Codex metadata must augment rather than erase earlier facts",
        )

    def test_b3_codex_recent_prefers_tool_name_with_type_fallback(self):
        codex_root = self.root / "codex-b3"
        session = "44444444-4444-4444-8444-444444444444"
        self.build_codex(
            codex_root,
            REPO,
            session,
            activity=(
                {"type": "custom_tool_call", "name": "exec"},
                {"type": "function_call", "name": "write_stdin"},
                {"type": "unlabelled_activity"},
            ),
        )

        document = self.invoke(*self.json_args(codex=codex_root, tail=3))
        rows = document["sessions"]
        self.assertEqual(len(rows), 1, "the Codex activity rollout was not reported")
        self.assertEqual(
            rows[0]["recent"],
            ["exec", "write_stdin", "unlabelled_activity"],
            "Codex recent activity must expose tool names and fall back to type",
        )

    def test_b4_tail_zero_returns_no_activity_for_every_harness(self):
        claude_root = self.root / "claude-b4"
        codex_root = self.root / "codex-b4"
        grok_root = self.root / "grok-b4"
        self.build_claude(claude_root, REPO, "claude-b4", activity="Read")
        self.build_codex(
            codex_root,
            REPO,
            "55555555-5555-4555-8555-555555555555",
            activity=({"type": "custom_tool_call", "name": "exec"},),
        )
        self.build_grok(grok_root, REPO, "grok-b4", activity="search_code")

        document = self.invoke(
            *self.json_args(
                claude=claude_root,
                codex=codex_root,
                grok=grok_root,
                tail=0,
            )
        )
        recent = {
            row["harness"]: row["recent"] for row in document["sessions"]
        }
        self.assertEqual(
            recent,
            {"claude": [], "codex": [], "grok": []},
            "--tail 0 must suppress non-empty history for every harness",
        )

    def test_b5_claude_repo_filter_uses_cwd_not_lossy_slug(self):
        claude_root = self.root / "claude-b5"
        actual_repo = "/a/b-c"
        colliding_repo = "/a-b/c"
        session = "claude-b5"
        self.assertEqual(
            "-" + "-".join(actual_repo.strip("/").split("/")),
            "-" + "-".join(colliding_repo.strip("/").split("/")),
            "the two repo paths do not plant the required slug collision",
        )
        self.build_claude(claude_root, actual_repo, session)

        actual = self.invoke(
            *self.json_args(repo=actual_repo, claude=claude_root)
        )
        collision = self.invoke(
            *self.json_args(repo=colliding_repo, claude=claude_root)
        )
        self.assertEqual(
            (
                [row["session"] for row in actual["sessions"]],
                collision["sessions"],
            ),
            ([session], []),
            "Claude --repo matching must verify the cwd stored in each entry",
        )

    def test_b6_liveness_uses_fractional_idle_but_reports_integer_idle(self):
        claude_root = self.root / "claude-b6"
        codex_root = self.root / "codex-b6"
        grok_root = self.root / "grok-b6"
        stale_sessions = {
            "claude": "claude-fractionally-stale",
            "codex": "66666666-6666-4666-8666-666666666661",
            "grok": "grok-fractionally-stale",
        }
        boundary_sessions = {
            "claude": "claude-exact-boundary",
            "codex": "66666666-6666-4666-8666-666666666662",
            "grok": "grok-exact-boundary",
        }
        streams = [
            self.build_claude(
                claude_root,
                REPO,
                stale_sessions["claude"],
                idle=300.8,
            ),
            self.build_codex(
                codex_root,
                REPO,
                stale_sessions["codex"],
                idle=300.8,
            ),
            self.build_grok(
                grok_root,
                REPO,
                stale_sessions["grok"],
                idle=300.8,
            ),
        ]
        boundary_streams = [
            self.build_claude(
                claude_root,
                REPO,
                boundary_sessions["claude"],
                idle=300.0,
            ),
            self.build_codex(
                codex_root,
                REPO,
                boundary_sessions["codex"],
                idle=300.0,
            ),
            self.build_grok(
                grok_root,
                REPO,
                boundary_sessions["grok"],
                idle=300.0,
            ),
        ]
        for stream in streams:
            self.assertGreater(
                NOW - stream.stat().st_mtime,
                300,
                f"the fractionally stale mtime is not outside: {stream}",
            )
        for stream in boundary_streams:
            self.assertAlmostEqual(
                NOW - stream.stat().st_mtime,
                300.0,
                places=3,
                msg=f"the mtime is not exactly on the boundary: {stream}",
            )

        document = self.invoke(
            *self.json_args(
                claude=claude_root,
                codex=codex_root,
                grok=grok_root,
            )
        )
        rows = document["sessions"]
        observed = {
            (row["harness"], row["session"]): row for row in rows
        }
        expected = {
            (harness, session)
            for harness, session in boundary_sessions.items()
        }
        self.assertEqual(
            set(observed),
            expected,
            (
                "fractional idle must be compared before integer "
                "presentation truncation for every harness"
            ),
        )
        for key, row in observed.items():
            with self.subTest(session=key):
                self.assertIs(type(row["idle_seconds"]), int)
                self.assertEqual(row["idle_seconds"], 300)


if __name__ == "__main__":
    unittest.main()
