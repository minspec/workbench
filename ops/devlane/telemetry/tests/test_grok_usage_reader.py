"""Contracts for reading cumulative Grok usage from updates.jsonl."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve()
USAGE = HERE.parents[1] / "usage.py"

BASE_EPOCH = 1_787_306_400
REPO = "/home/work/projects/minspec/workbench"
MODEL = "grok-4.6"
GROK_GAP = "grok usage not yet parsed: updates.jsonl turn_completed"
MARKER = "GROKREADERCONTENTMARKER"

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

COMPLETE_A = [
    {
        "inputTokens": 10,
        "outputTokens": 2,
        "totalTokens": 12,
        "cachedReadTokens": 1,
        "cacheCreationTokens": 1,
        "reasoningTokens": 1,
        "modelCalls": 1,
        "apiDurationMs": 100,
        "costUsdTicks": 100,
        "numTurns": 1,
    },
    {
        "inputTokens": 30,
        "outputTokens": 10,
        "totalTokens": 40,
        "cachedReadTokens": 4,
        "cacheCreationTokens": 1,
        "reasoningTokens": 2,
        "modelCalls": 2,
        "apiDurationMs": 300,
        "costUsdTicks": 300,
        "numTurns": 2,
    },
    {
        "inputTokens": 7,
        "outputTokens": 3,
        "totalTokens": 10,
        "cachedReadTokens": 2,
        "reasoningTokens": 1,
        "modelCalls": 1,
        "apiDurationMs": 80,
        "costUsdTicks": 80,
        "numTurns": 1,
    },
]

COMPLETE_B = [
    {
        "inputTokens": 5,
        "outputTokens": 4,
        "totalTokens": 9,
        "cachedReadTokens": 1,
        "cacheCreationTokens": 2,
        "reasoningTokens": 1,
        "modelCalls": 1,
        "apiDurationMs": 50,
        "costUsdTicks": 20,
        "numTurns": 1,
    }
]


def load_module(testcase):
    testcase.assertTrue(
        USAGE.is_file(),
        f"{USAGE} is missing; the usage reader contract requires it",
    )
    spec = importlib.util.spec_from_file_location(
        "minspec_grok_usage_reader",
        USAGE,
    )
    testcase.assertIsNotNone(spec)
    testcase.assertIsNotNone(spec.loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path, entries):
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries)
    )


class GrokUsageReader(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="grok-usage-reader-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.grok = self.root / "grok"
        self.module = load_module(self)

    def write_session(self, session_id, usage):
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
                    "session_summary": f"Private content {MARKER}",
                }
            )
        )
        entries = [
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "kind": "tool",
                        "detail": MARKER,
                        "usage": {"inputTokens": 999_999},
                    }
                },
                "timestamp": BASE_EPOCH + 20,
            }
        ]
        entries.extend(
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
        )
        write_jsonl(session / "updates.jsonl", entries)
        return session

    def rows(self):
        return list(self.module.grok_sessions(self.grok, REPO))

    def run_usage(self, *args):
        return subprocess.run(
            [
                sys.executable,
                str(USAGE),
                *args,
                "--claude-dir",
                str(self.root / "empty-claude"),
                "--codex-dir",
                str(self.root / "empty-codex"),
                "--grok-dir",
                str(self.grok),
                "--repo",
                REPO,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def json_document(self, *args):
        proc = self.run_usage(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"{USAGE} did not emit one JSON document: {error}")

    def test_resets_bank_per_run_maxima_and_keep_the_legacy_gap(self):
        self.write_session("usage-reset", RESET_USAGE)
        self.write_session("legacy-no-usage", [])

        rows = self.rows()
        self.assertEqual(
            len(rows),
            2,
            "the two planted Grok sessions were not both read",
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
            (
                "Grok cumulative usage must sum each run's maxima, "
                "not take the final event or sum every event"
            ),
        )
        self.assertEqual(
            set(used["tokens"]),
            {"input", "cached", "output", "total"},
        )
        self.assertEqual(used["reasoning"], 43)
        self.assertIn("cost_usd_ticks", used)
        self.assertIsNone(
            used["cost_usd_ticks"],
            "a missing cost currency is a gap, not zero or a partial sum",
        )
        self.assertIn(
            "incomplete",
            used.get("note", "").lower(),
            "usageIsIncomplete must remain visible to the caller",
        )
        self.assertNotEqual(used.get("note"), GROK_GAP)

        legacy = by_session["legacy-no-usage"]
        self.assertIsNone(legacy["tokens"])
        self.assertEqual(
            legacy["note"],
            GROK_GAP,
            "pre-upgrade Grok sessions must retain the exact gap note",
        )

    def test_complete_cost_ticks_and_tokens_are_aggregated_by_the_report(self):
        self.write_session("complete-a", COMPLETE_A)
        self.write_session("complete-b", COMPLETE_B)

        sessions = self.json_document("sessions", "--json")["sessions"]
        self.assertEqual(len(sessions), 2)
        by_session = {row["session"]: row for row in sessions}

        self.assertEqual(
            by_session["complete-a"]["tokens"],
            {
                "input": 37,
                "cached": 7,
                "output": 13,
                "total": 50,
            },
        )
        self.assertEqual(
            by_session["complete-a"]["cost_usd_ticks"],
            380,
        )
        self.assertEqual(by_session["complete-a"]["reasoning"], 3)
        self.assertEqual(
            by_session["complete-b"]["tokens"],
            {
                "input": 5,
                "cached": 3,
                "output": 4,
                "total": 9,
            },
        )
        self.assertEqual(
            by_session["complete-b"]["cost_usd_ticks"],
            20,
        )
        for row in by_session.values():
            self.assertNotEqual(row.get("note"), GROK_GAP)
            if row.get("note") is not None:
                self.assertIn("pars", row["note"].lower())

        report = self.json_document("report", "--json")
        aggregate = report["by_harness"]["grok"]
        self.assertEqual(aggregate["sessions"], 2)
        self.assertEqual(aggregate["counted"], 2)
        self.assertEqual(
            aggregate["tokens"],
            {
                "input": 42,
                "cached": 10,
                "output": 17,
                "total": 59,
            },
        )
        self.assertIn("cost_usd_ticks", aggregate)
        self.assertEqual(aggregate["cost_usd_ticks"], 400)

        plain_report = self.run_usage("report")
        self.assertEqual(
            plain_report.returncode,
            0,
            plain_report.stderr,
        )
        self.assertNotIn("tokens=unrecorded", plain_report.stdout)
        self.assertIn("total=59", plain_report.stdout)
        self.assertIn("cost_usd_ticks=400", plain_report.stdout)

        plain_sessions = self.run_usage("sessions")
        self.assertEqual(
            plain_sessions.returncode,
            0,
            plain_sessions.stderr,
        )
        for session_id, ticks in (
            ("complete-a", 380),
            ("complete-b", 20),
        ):
            lines = [
                line
                for line in plain_sessions.stdout.splitlines()
                if session_id in line
            ]
            self.assertEqual(
                len(lines),
                1,
                f"expected one plain row for {session_id}",
            )
            self.assertIn("total=", lines[0])
            self.assertNotIn("unrecorded", lines[0])
            self.assertIn(f"cost_usd_ticks={ticks}", lines[0])

        plain = (
            plain_report.stdout + "\n" + plain_sessions.stdout
        ).lower()
        if "$" in plain or "cost_usd=" in plain:
            self.assertIn(
                "inferred",
                plain,
                (
                    "derived USD is permitted only when its scale is "
                    "explicitly marked inferred"
                ),
            )
        self.assertNotIn(
            MARKER,
            plain,
            "session content leaked through the usage reader",
        )


class SkepticAccountingShapes(unittest.TestCase):
    """Round-1 skeptic findings, pinned as units against the module
    (live stream 019fb283: totals shrink while numTurns rises)."""

    def setUp(self):
        self.module = load_module(self)

    def totals(self, events):
        return self.module._grok_usage_totals(events)

    def test_a_shrink_without_a_turns_drop_starts_a_new_run(self):
        events = [
            {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120,
             "costUsdTicks": 100, "numTurns": 12},
            {"inputTokens": 30, "outputTokens": 10, "totalTokens": 40,
             "costUsdTicks": 30, "numTurns": 15},
        ]
        self.assertLess(events[1]["totalTokens"], events[0]["totalTokens"])
        self.assertGreater(events[1]["numTurns"], events[0]["numTurns"])
        tokens, _, cost, _ = self.totals(events)
        self.assertEqual(tokens["total"], 160,
                         "a totals shrink with rising turns must split runs")
        self.assertEqual(tokens["input"], 130)
        self.assertEqual(cost, 130)

    def test_within_a_run_the_last_report_wins_over_the_maximum(self):
        events = [
            {"inputTokens": 40, "outputTokens": 10, "totalTokens": 50,
             "costUsdTicks": 50, "numTurns": 1},
            {"inputTokens": 35, "outputTokens": 45, "totalTokens": 80,
             "costUsdTicks": 80, "numTurns": 2},
        ]
        self.assertLess(events[1]["inputTokens"], events[0]["inputTokens"])
        tokens, _, cost, _ = self.totals(events)
        self.assertEqual(tokens["input"], 35,
                         "max-merge kept a superseded cumulative report")
        self.assertEqual(tokens["total"], 80)
        self.assertEqual(cost, 80)

    def test_equal_totals_stay_in_one_run(self):
        events = [
            {"inputTokens": 60, "outputTokens": 20, "totalTokens": 80,
             "costUsdTicks": 80, "numTurns": 1},
            {"inputTokens": 60, "outputTokens": 20, "totalTokens": 80,
             "costUsdTicks": 90, "numTurns": 2},
        ]
        self.assertEqual(events[0]["totalTokens"], events[1]["totalTokens"])
        tokens, _, cost, _ = self.totals(events)
        self.assertEqual(tokens["total"], 80,
                         "equal cumulative totals split a run that never"
                         " reset (a <= split double-counts)")
        self.assertEqual(cost, 90)

    def test_equal_turns_stay_in_one_run(self):
        events = [
            {"inputTokens": 40, "outputTokens": 10, "totalTokens": 50,
             "cachedReadTokens": 5, "cacheCreationTokens": 2,
             "costUsdTicks": 50, "numTurns": 1},
            {"inputTokens": 60, "outputTokens": 20, "totalTokens": 80,
             "cachedReadTokens": 9, "costUsdTicks": 80, "numTurns": 1},
        ]
        tokens, _, cost, _ = self.totals(events)
        self.assertEqual(tokens["total"], 80,
                         "equal turns split a run that never reset")
        self.assertEqual(tokens["cached"], 11,
                         "an omitted currency erased the run's report")
        self.assertEqual(cost, 80)


class SkepticReportShapes(GrokUsageReader):
    def test_one_costless_session_makes_the_aggregate_cost_a_gap(self):
        self.write_session("with-cost", COMPLETE_B)
        self.write_session("without-cost", [
            {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10,
             "numTurns": 1},
        ])
        report = self.json_document("report", "--json")
        aggregate = report["by_harness"]["grok"]
        self.assertEqual(aggregate["counted"], 2)
        self.assertIn("cost_usd_ticks", aggregate)
        self.assertIsNone(
            aggregate["cost_usd_ticks"],
            "a partial cost sum was passed off as the aggregate")
        plain = self.run_usage("report")
        self.assertIn("cost_usd_ticks=unrecorded", plain.stdout,
                      "an unknown aggregate cost must say so explicitly")
        self.assertNotRegex(plain.stdout, r"cost_usd_ticks=\d",
                            "the plain report printed a partial cost sum")

    def test_incompleteness_propagates_into_every_report_format(self):
        self.write_session("incomplete-run", RESET_USAGE)
        self.write_session("complete-run", COMPLETE_B)
        rows = {r["session"]: r for r in
                self.json_document("sessions", "--json")["sessions"]}
        self.assertIs(rows["incomplete-run"]["incomplete"], True,
                      "usageIsIncomplete must be a structured row flag")
        self.assertIs(rows["complete-run"]["incomplete"], False)
        report = self.json_document("report", "--json")
        aggregate = report["by_harness"]["grok"]
        self.assertEqual(
            aggregate.get("incomplete_sessions"), 1,
            "the report presented incomplete measurements as verified")
        plain = self.run_usage("report")
        self.assertIn("incomplete=1", plain.stdout,
                      "the plain report hid the incompleteness")
        sessions_plain = self.run_usage("sessions")
        # the plain row truncates ids to 12 chars
        line = next(l for l in sessions_plain.stdout.splitlines()
                    if "incomplete-r" in l)
        self.assertIn("(incomplete)", line)
        self.assertIn("cost_usd_ticks=unrecorded", line)

    def test_parsed_rows_never_carry_session_content(self):
        self.write_session("leaky", COMPLETE_B)
        document = self.json_document("sessions", "--json")
        self.assertNotIn(MARKER, json.dumps(document),
                         "session content leaked into parsed grok rows")


if __name__ == "__main__":
    unittest.main()
