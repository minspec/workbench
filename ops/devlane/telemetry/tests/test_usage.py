"""The usage reporter, against fixtures shaped like the real session stores.

Each fixture replicates a shape measured on 2026-08-21 from the live
stores: Claude's per-message usage, Codex's cumulative token_count events,
and Grok's summary.json — which records NO token usage, a gap the report
must state rather than estimate around.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

USAGE = Path(__file__).resolve().parents[1] / "usage.py"
REPO = "/home/work/projects/minspec/workbench"


def line(**kw):
    return json.dumps(kw) + "\n"


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="telemetry-"))
        self.addCleanup(__import__("shutil").rmtree, self.home, True)

        # Claude: <dir>/<project-slug>/<session>.jsonl, usage per message.
        proj = self.home / "claude" / "-home-work-projects-minspec-workbench"
        proj.mkdir(parents=True)
        (proj / "aaaa.jsonl").write_text(
            line(timestamp="2026-08-21T10:00:00.000Z",
                 message={"model": "claude-fable-5",
                          "usage": {"input_tokens": 10,
                                    "cache_creation_input_tokens": 1000,
                                    "cache_read_input_tokens": 5000,
                                    "output_tokens": 200}})
            + line(timestamp="2026-08-21T10:05:00.000Z",
                   message={"model": "claude-fable-5",
                            "usage": {"input_tokens": 20,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 6000,
                                      "output_tokens": 300}}))
        other = self.home / "claude" / "-somewhere-else"
        other.mkdir()
        (other / "bbbb.jsonl").write_text(
            line(timestamp="2026-08-21T09:00:00.000Z",
                 message={"model": "claude-fable-5",
                          "usage": {"input_tokens": 999, "output_tokens": 999}}))

        # Codex: sessions/Y/M/D/rollout-*.jsonl, cumulative token_count.
        day = self.home / "codex" / "sessions" / "2026" / "08" / "21"
        day.mkdir(parents=True)
        (day / "rollout-2026-08-21T10-00-00-cccc.jsonl").write_text(
            line(timestamp="2026-08-21T10:00:00.000Z", type="session_meta",
                 payload={"id": "cccc", "cwd": REPO,
                          "timestamp": "2026-08-21T10:00:00.000Z"})
            + line(timestamp="2026-08-21T10:02:00.000Z", type="event_msg",
                   payload={"type": "token_count",
                            "info": {"total_token_usage": {
                                "input_tokens": 100, "cached_input_tokens": 50,
                                "output_tokens": 40, "total_tokens": 140}}})
            + line(timestamp="2026-08-21T10:09:00.000Z", type="event_msg",
                   payload={"type": "token_count",
                            "info": {"total_token_usage": {
                                "input_tokens": 400, "cached_input_tokens": 300,
                                "output_tokens": 90, "total_tokens": 490}}}))

        # Grok: sessions/<urlencoded-cwd>/<id>/summary.json — no tokens.
        gdir = (self.home / "grok" / "sessions"
                / quote(REPO, safe="") / "dddd")
        gdir.mkdir(parents=True)
        (gdir / "summary.json").write_text(json.dumps({
            "info": {"id": "dddd", "cwd": REPO},
            "session_summary": "SESSIONPROMPTTEXT", "num_messages": 177,
            "created_at": "2026-08-21T10:00:00.000000000Z",
            "updated_at": "2026-08-21T10:20:00.000000000Z",
            "current_model_id": "grok-4.6",
            "git_root_dir": REPO + "/"}))

    def run_usage(self, *args):
        return subprocess.run(
            [sys.executable, str(USAGE), *args,
             "--claude-dir", str(self.home / "claude"),
             "--codex-dir", str(self.home / "codex"),
             "--grok-dir", str(self.home / "grok"),
             "--repo", REPO],
            capture_output=True, text=True, check=False)

    def sessions(self):
        proc = self.run_usage("sessions", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["sessions"]


class SessionsAreNormalised(Fixtures):
    def test_all_three_harnesses_appear_once(self):
        rows = self.sessions()
        self.assertEqual(sorted(r["harness"] for r in rows),
                         ["claude", "codex", "grok"])

    def test_the_repo_filter_excludes_other_projects(self):
        for row in self.sessions():
            self.assertNotEqual(row.get("session"), "bbbb",
                                "a session from another repo leaked in")

    def test_claude_tokens_are_summed_per_message(self):
        row = next(r for r in self.sessions() if r["harness"] == "claude")
        self.assertEqual(row["tokens"]["output"], 500)
        self.assertEqual(row["tokens"]["input"], 30)
        self.assertEqual(row["tokens"]["cached"], 12000)

    def test_codex_takes_the_last_cumulative_count(self):
        row = next(r for r in self.sessions() if r["harness"] == "codex")
        self.assertEqual(row["tokens"]["total"], 490,
                         "cumulative counts must not be summed")
        self.assertEqual(row["tokens"]["output"], 90)

    def test_grok_states_its_gap_instead_of_inventing(self):
        row = next(r for r in self.sessions() if r["harness"] == "grok")
        self.assertIsNone(row["tokens"])
        self.assertIn("not yet parsed", row["note"])
        self.assertEqual(row["messages"], 177)
        self.assertEqual(row["model"], "grok-4.6")


class TheReportAggregates(Fixtures):
    def test_totals_per_harness_and_the_gap_stated(self):
        proc = self.run_usage("report")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("claude", proc.stdout)
        self.assertIn("490", proc.stdout, "codex total")
        self.assertIn("not yet parsed", proc.stdout, "the grok gap is stated")

    def test_the_report_never_prints_prompt_text(self):
        # The stores hold prompts and summaries; the report is aggregates
        # only. The fixture plants a distinctive marker to catch a leak.
        proc = self.run_usage("report")
        self.assertNotIn("SESSIONPROMPTTEXT", proc.stdout,
                         "session content leaked into the report")


if __name__ == "__main__":
    unittest.main()
