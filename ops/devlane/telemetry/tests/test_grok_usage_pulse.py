"""Contracts for Grok usage in live pulse rows."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve()
PULSE = HERE.parents[1] / "pulse.py"

BASE_EPOCH = 1_787_306_400
NOW = BASE_EPOCH + 1_000
REPO = "/home/work/projects/minspec/workbench"
MODEL = "grok-4.6"

ROW_KEYS = {
    "harness",
    "session",
    "model",
    "age_seconds",
    "idle_seconds",
    "tokens",
    "recent",
}
TOKEN_KEYS = {"input", "cached", "output", "total"}

RESET_USAGE = [
    {
        "inputTokens": 100,
        "outputTokens": 20,
        "totalTokens": 120,
        "cachedReadTokens": 30,
        "cacheCreationTokens": 5,
        "reasoningTokens": 7,
        "modelCalls": 1,
        "apiDurationMs": 1_000,
        "costUsdTicks": 1_100,
        "numTurns": 1,
        "modelUsage": {
            MODEL: {
                "inputTokens": 100,
                "outputTokens": 20,
                "totalTokens": 120,
                "cachedReadTokens": 30,
                "cacheCreationTokens": 5,
                "reasoningTokens": 7,
                "modelCalls": 1,
                "apiDurationMs": 1_000,
                "costUsdTicks": 1_100,
            }
        },
    },
    {
        "inputTokens": 300,
        "outputTokens": 80,
        "totalTokens": 380,
        "cachedReadTokens": 90,
        "reasoningTokens": 40,
        "modelCalls": 3,
        "apiDurationMs": 3_500,
        "costUsdTicks": 2_500,
        "numTurns": 3,
        "modelUsage": {
            MODEL: {
                "inputTokens": 300,
                "outputTokens": 80,
                "totalTokens": 380,
                "cachedReadTokens": 90,
                "reasoningTokens": 40,
                "modelCalls": 3,
                "apiDurationMs": 3_500,
                "costUsdTicks": 2_500,
            }
        },
    },
    {
        "inputTokens": 40,
        "outputTokens": 10,
        "totalTokens": 50,
        "cachedReadTokens": 7,
        "cacheCreationTokens": 2,
        "reasoningTokens": 3,
        "modelCalls": 1,
        "apiDurationMs": 500,
        "numTurns": 1,
        "usageIsIncomplete": True,
        "modelUsage": {
            MODEL: {
                "inputTokens": 40,
                "outputTokens": 10,
                "totalTokens": 50,
                "cachedReadTokens": 7,
                "cacheCreationTokens": 2,
                "reasoningTokens": 3,
                "modelCalls": 1,
                "apiDurationMs": 500,
            }
        },
    },
]


def write_jsonl(path, entries):
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries)
    )


class GrokUsagePulse(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="grok-usage-pulse-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.grok = self.root / "grok"
        self.empty_claude = self.root / "empty-claude"
        self.empty_codex = self.root / "empty-codex"
        self.empty_claude.mkdir()
        self.empty_codex.mkdir()

    def set_mtime(self, path, idle):
        timestamp = NOW - idle
        os.utime(path, (timestamp, timestamp))
        self.assertAlmostEqual(
            path.stat().st_mtime,
            timestamp,
            places=3,
            msg=f"the liveness mtime plant did not land on {path}",
        )

    def write_session(
        self,
        session_id,
        usage,
        *,
        tools=(),
        updates_idle=10.0,
        events_idle=10.0,
    ):
        session = (
            self.grok
            / "sessions"
            / quote(REPO, safe="")
            / session_id
        )
        session.mkdir(parents=True)
        (session / "summary.json").write_text(
            json.dumps(
                {
                    "info": {"id": session_id, "cwd": REPO},
                    "created_at": "2026-08-21T10:00:00.000000000Z",
                    "updated_at": "2026-08-21T10:20:00.000000000Z",
                    "num_messages": 9,
                    "current_model_id": MODEL,
                }
            )
        )

        updates = [
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "turn_completed",
                        "usage": value,
                    }
                },
                "timestamp": BASE_EPOCH + 100 + index,
            }
            for index, value in enumerate(usage)
        ]
        events = [
            {
                "type": "tool_started",
                "tool_name": tool,
                "ts": (
                    "2026-08-21T10:00:"
                    f"{30 + index:02d}.000Z"
                ),
            }
            for index, tool in enumerate(tools)
        ]

        updates_path = session / "updates.jsonl"
        events_path = session / "events.jsonl"
        write_jsonl(updates_path, updates)
        write_jsonl(events_path, events)
        self.set_mtime(updates_path, updates_idle)
        self.set_mtime(events_path, events_idle)
        return session

    def run_pulse(self, *, json_output=True):
        args = [
            sys.executable,
            str(PULSE),
            "--repo",
            REPO,
            "--now",
            str(NOW),
            "--live-window",
            "300",
            "--tail",
            "10",
            "--claude-dir",
            str(self.empty_claude),
            "--codex-dir",
            str(self.empty_codex),
            "--grok-dir",
            str(self.grok),
        ]
        if json_output:
            args.append("--json")
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )

    def json_rows(self):
        proc = self.run_pulse()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        try:
            document = json.loads(proc.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"{PULSE} did not emit one JSON document: {error}")
        self.assertEqual(set(document), {"sessions"})
        return document["sessions"]

    def test_live_usage_uses_per_run_maxima_and_legacy_tokens_stay_none(self):
        self.write_session(
            "usage-reset",
            RESET_USAGE,
            tools=("search_code",),
        )
        self.write_session(
            "legacy-no-usage",
            [],
            tools=("read_file",),
        )

        rows = self.json_rows()
        self.assertEqual(
            len(rows),
            2,
            "the two planted live Grok sessions were not reported",
        )
        by_session = {row["session"]: row for row in rows}
        self.assertEqual(
            set(by_session),
            {"legacy-no-usage", "usage-reset"},
        )

        used = by_session["usage-reset"]
        self.assertEqual(
            used["tokens"],
            {
                "input": 340,
                "cached": 104,
                "output": 90,
                "total": 430,
            },
            "pulse did not sum the maxima from both Grok runs",
        )
        self.assertEqual(set(used["tokens"]), TOKEN_KEYS)
        self.assertEqual(
            set(used),
            ROW_KEYS,
            "reasoning or cost escaped into the closed pulse row shape",
        )

        legacy = by_session["legacy-no-usage"]
        self.assertIsNone(
            legacy["tokens"],
            "a Grok session without usage must remain an explicit gap",
        )
        self.assertEqual(set(legacy), ROW_KEYS)

        plain = self.run_pulse(json_output=False)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        usage_lines = [
            line
            for line in plain.stdout.splitlines()
            if "usage-reset" in line
        ]
        legacy_lines = [
            line
            for line in plain.stdout.splitlines()
            if "legacy-no-usage" in line
        ]
        self.assertEqual(len(usage_lines), 1)
        self.assertEqual(len(legacy_lines), 1)
        self.assertIn("tokens=430", usage_lines[0])
        self.assertNotIn("tokens=unrecorded", usage_lines[0])
        self.assertIn("tokens=unrecorded", legacy_lines[0])

    def test_usage_updates_drive_liveness_but_never_enter_recent_names(self):
        self.write_session(
            "usage-is-not-activity",
            RESET_USAGE[:2],
            tools=("search_code",),
            updates_idle=10.0,
            events_idle=600.0,
        )

        rows = self.json_rows()
        self.assertEqual(
            len(rows),
            1,
            (
                "a fresh usage-bearing updates stream must keep the "
                "Grok session live"
            ),
        )
        row = rows[0]
        self.assertEqual(row["session"], "usage-is-not-activity")
        self.assertEqual(
            row["idle_seconds"],
            10,
            "liveness did not use the freshest Grok stream",
        )
        self.assertEqual(
            row["recent"],
            ["search_code"],
            (
                "turn_completed usage updates are accounting records, "
                "not recent tool names"
            ),
        )
        self.assertNotIn("turn_completed", row["recent"])
        self.assertNotIn("session/update", row["recent"])
        self.assertEqual(
            row["tokens"],
            {
                "input": 300,
                "cached": 95,
                "output": 80,
                "total": 380,
            },
        )
        self.assertEqual(set(row["tokens"]), TOKEN_KEYS)
        self.assertEqual(set(row), ROW_KEYS)


class SkepticMirrorAccounting(GrokUsagePulse):
    def test_pulse_mirror_pins_last_report_and_equal_totals(self):
        self.write_session("mirror", [
            {"inputTokens": 40, "outputTokens": 10, "totalTokens": 50,
             "numTurns": 1},
            {"inputTokens": 35, "outputTokens": 45, "totalTokens": 80,
             "numTurns": 2},
            {"inputTokens": 36, "outputTokens": 44, "totalTokens": 80,
             "numTurns": 2},
        ])
        rows = self.json_rows()
        row = next(r for r in rows if r["session"] == "mirror")
        self.assertEqual(
            row["tokens"],
            {"input": 36, "cached": 0, "output": 44, "total": 80},
            "pulse's mirror must keep equal totals in one run and let"
            " the last report supersede the maximum",
        )


class SkepticCancelledTurns(GrokUsagePulse):
    def test_a_cancelled_turn_without_usage_stays_out_of_recent(self):
        session = self.write_session(
            "cancelled", [], tools=("search_code",))
        updates_path = session / "updates.jsonl"
        cancelled = {
            "method": "session/update",
            "params": {"update": {
                "sessionUpdate": "turn_completed",
                "prompt_id": "p-1",
                "stop_reason": "cancelled",
            }},
            "timestamp": BASE_EPOCH + 200,
        }
        with updates_path.open("a") as handle:
            handle.write(json.dumps(cancelled) + "\n")
        planted = updates_path.read_text()
        self.assertIn('"stop_reason": "cancelled"', planted,
                      "the cancelled-turn plant did not land")
        self.set_mtime(updates_path, 10.0)
        rows = self.json_rows()
        row = next(r for r in rows if r["session"] == "cancelled")
        self.assertNotIn("session/update", row["recent"],
                         "a usage-less turn_completed polluted recent")
        self.assertEqual(row["recent"], ["search_code"])
        self.assertIsNone(row["tokens"],
                          "a cancelled turn without usage invented tokens")


if __name__ == "__main__":
    unittest.main()
