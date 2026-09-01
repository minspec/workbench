"""Pins for the seams Codex's audit planted through (PR #31).

Each case proves its fixture landed before asserting: a plant that
silently failed to plant answers a question about nothing.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

WORTH = Path(__file__).resolve().parents[1] / "worth.py"
NOW = "2026-08-22T12:00:00.000Z"


class SeamCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="worth-seams-"))
        self.addCleanup(
            lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b",
                        "dev"], check=True)
        self.claude = self.tmp / "claude"
        self.codex = self.tmp / "codex"
        self.grok = self.tmp / "grok"

    def write_lines(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        planted = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(planted, rows, "the plant did not land intact")
        return path

    def claude_row(self, mid, stamp, usage):
        return {"type": "assistant", "timestamp": stamp,
                "cwd": str(self.repo), "sessionId": "s",
                "message": {"id": mid, "usage": usage, "content": []}}

    def grok_session(self, name, updates=None, raw_updates=None):
        session = self.grok / "sessions" / quote(str(self.repo), safe="") / name
        session.mkdir(parents=True)
        (session / "summary.json").write_text(json.dumps(
            {"info": {"id": name, "cwd": str(self.repo)},
             "created_at": "2026-08-22T10:00:00.000000000Z",
             "updated_at": "2026-08-22T10:20:00.000000000Z"}))
        if raw_updates is not None:
            (session / "updates.jsonl").write_text(raw_updates)
            self.assertEqual((session / "updates.jsonl").read_text(),
                             raw_updates)
        elif updates is not None:
            self.write_lines(session / "updates.jsonl", updates)
        return session

    def worth(self, verb, *args):
        proc = subprocess.run(
            [sys.executable, str(WORTH), verb, *args, "--now", NOW,
             "--repo", str(self.repo),
             "--claude-dir", str(self.claude),
             "--codex-dir", str(self.codex),
             "--grok-dir", str(self.grok),
             "--format", "json"],
            capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def slug(self):
        return "-" + "-".join(str(self.repo).strip("/").split("/"))


class EmptyUsageIsNotAMeasurement(SeamCase):
    def test_an_empty_claude_usage_dict_is_unrecorded_not_zero(self):
        path = self.write_lines(
            self.claude / self.slug() / "s.jsonl",
            [self.claude_row("m1", "2026-08-22T10:00:00.000Z", {})])
        self.assertIn('"usage": {}', path.read_text())
        record = self.worth("report")["cost"]["claude"]
        self.assertEqual(record.get("sessions"), 0)
        self.assertEqual(record.get("tokens"), "unrecorded")

    def test_a_keyless_grok_usage_dict_is_not_a_run(self):
        at = int(datetime(2026, 8, 22, 10, 0, 0,
                          tzinfo=timezone.utc).timestamp())
        self.grok_session("g", updates=[
            {"method": "session/update", "timestamp": at,
             "params": {"update": {"sessionUpdate": "turn_completed",
                                   "usage": {"unrelated": 1}}}}])
        record = self.worth("report")["cost"]["grok"]
        self.assertEqual(record.get("counted"), "0/1")
        self.assertEqual(record.get("tokens"), "unrecorded")


class KeylessCodexUsageIsNotAMeasurement(SeamCase):
    def test_a_keyless_token_count_is_unrecorded_not_zero(self):
        rows = [
            {"timestamp": "2026-08-22T10:00:00.000Z",
             "type": "session_meta",
             "payload": {"id": "cx", "cwd": str(self.repo)}},
            {"timestamp": "2026-08-22T10:01:00.000Z",
             "type": "event_msg",
             "payload": {"type": "token_count",
                         "info": {"total_token_usage": {}}}},
        ]
        path = self.write_lines(
            self.codex / "sessions" / "2026" / "08" / "22"
            / "rollout-2026-08-22T10-00-00-cx.jsonl", rows)
        self.assertIn('"total_token_usage": {}', path.read_text())
        record = self.worth("report")["cost"]["codex"]
        self.assertEqual(record.get("sessions"), 0)
        self.assertEqual(set(record), {"sessions", "tokens"})


class MalformedTimestampsAreGapsNotCrashes(SeamCase):
    def test_a_non_string_claude_timestamp_gaps_the_session(self):
        """Scenario: a malformed store timestamp is a gap, not a crash"""
        path = self.write_lines(
            self.claude / self.slug() / "s.jsonl",
            [self.claude_row("m1", 12345, {"output_tokens": 5})])
        self.assertIn('"timestamp": 12345', path.read_text())
        record = self.worth("report")["cost"]["claude"]
        self.assertEqual(record.get("sessions"), 0)
        self.assertEqual(record.get("tokens"), "unrecorded")


class GrokDenominatorHoldsItsHoles(SeamCase):
    USAGE: ClassVar[dict] = {
        "inputTokens": 90, "cachedReadTokens": 0,
        "cacheCreationTokens": 0, "outputTokens": 10,
        "totalTokens": 100, "costUsdTicks": 7}

    def good_session(self):
        # 2026-08-22T10:01:40Z as an epoch — computed here so the
        # figure can never be a hand-typed recollection
        at = int(datetime(2026, 8, 22, 10, 1, 40,
                          tzinfo=timezone.utc).timestamp())
        self.grok_session("g-good", updates=[
            {"method": "session/update", "timestamp": at,
             "params": {"update": {"sessionUpdate": "turn_completed",
                                   "usage": self.USAGE}}}])

    def test_an_unreadable_updates_file_still_holds_a_denominator_place(self):
        """Scenario: a grok session with unreadable spend holds its denominator place"""
        self.good_session()
        bad = self.grok_session("g-bad", raw_updates="{broken\n")
        self.assertEqual((bad / "updates.jsonl").read_text(), "{broken\n")
        record = self.worth("report")["cost"]["grok"]
        self.assertEqual(record.get("sessions"), 2)
        self.assertEqual(record.get("counted"), "1/2")
        self.assertEqual(record.get("total"), 100)

    def test_all_unrecorded_sessions_still_report_the_denominator(self):
        self.grok_session("g-bad", raw_updates="{broken\n")
        record = self.worth("report")["cost"]["grok"]
        self.assertEqual(record.get("counted"), "0/1")
        self.assertEqual(record.get("tokens"), "unrecorded")
        # exactly these keys: an added in=0/out=0 is a false figure
        self.assertEqual(set(record),
                         {"sessions", "runs", "counted", "tokens"})

    def test_an_iso_string_run_timestamp_is_a_gap_not_a_crash(self):
        self.good_session()
        iso = self.grok_session("g-iso", updates=[
            {"method": "session/update",
             "timestamp": "2026-08-22T10:05:00.000Z",
             "params": {"update": {"sessionUpdate": "turn_completed",
                                   "usage": {"totalTokens": 50,
                                             "outputTokens": 5}}}}])
        self.assertIn('"timestamp": "2026-08-22T10:05:00.000Z"',
                      (iso / "updates.jsonl").read_text())
        record = self.worth("report")["cost"]["grok"]
        self.assertEqual(record.get("sessions"), 2)
        self.assertEqual(record.get("counted"), "1/2")
        self.assertEqual(record.get("total"), 100)


class CodexHeavyTurnIsTheLargestStep(SeamCase):
    def test_the_heaviest_message_is_the_largest_delta_not_the_last(self):
        rows = [
            {"timestamp": "2026-08-22T10:00:00.000Z", "type": "session_meta",
             "payload": {"id": "cx", "cwd": str(self.repo)}},
        ]
        for stamp, out in (("2026-08-22T10:01:00.000Z", 40),
                           ("2026-08-22T10:02:00.000Z", 90),
                           ("2026-08-22T10:03:00.000Z", 100)):
            rows.append({"timestamp": stamp, "type": "event_msg",
                         "payload": {"type": "token_count", "info": {
                             "total_token_usage": {
                                 "input_tokens": 10, "cached_input_tokens": 0,
                                 "output_tokens": out,
                                 "total_tokens": 10 + out}}}})
        path = self.write_lines(
            self.codex / "sessions" / "2026" / "08" / "22"
            / "rollout-2026-08-22T10-00-00-cx.jsonl", rows)
        self.assertEqual(path.read_text().count("token_count"), 3)
        waste = self.worth("waste")
        heavy = [sig for sig in waste["signals"]
                 if "heavy" in json.dumps(sig) and sig.get("harness") == "codex"]
        self.assertEqual(len(heavy), 1)
        # deltas are 40, 50, 10 — the middle count is the heavy one
        self.assertEqual(heavy[0].get("at"), "2026-08-22T10:02:00.000Z")
        self.assertEqual(heavy[0].get("out_delta"), 50)
        self.assertEqual(heavy[0].get("out"), 90)


class SeamGitCase(SeamCase):
    def commit(self, files, subject, when):
        for rel, content in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)  # test-guard: allow
                self.assertEqual(path.read_bytes(), content)
            else:
                path.write_text(content)
                self.assertEqual(path.read_text(), content)
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
               "GIT_AUTHOR_NAME": "seam", "GIT_AUTHOR_EMAIL": "s@x",
               "GIT_COMMITTER_NAME": "seam", "GIT_COMMITTER_EMAIL": "s@x"}
        import os
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q",
                        "-m", subject], check=True,
                       env={**os.environ, **env})


class CodexBaselineSubtraction(SeamCase):
    def test_pre_window_spend_stays_out_of_the_window(self):
        """Scenario: pre-window spend stays out of the window"""
        rows = [{"timestamp": "2026-08-22T09:00:00.000Z",
                 "type": "session_meta",
                 "payload": {"id": "cx", "cwd": str(self.repo)}}]
        for stamp, out in (("2026-08-22T09:30:00.000Z", 10),
                           ("2026-08-22T10:30:00.000Z", 15)):
            rows.append({"timestamp": stamp, "type": "event_msg",
                         "payload": {"type": "token_count", "info": {
                             "total_token_usage": {
                                 "input_tokens": out * 2,
                                 "cached_input_tokens": 0,
                                 "output_tokens": out,
                                 "total_tokens": out * 3}}}})
        path = self.write_lines(
            self.codex / "sessions" / "2026" / "08" / "22"
            / "rollout-2026-08-22T09-00-00-cx.jsonl", rows)
        self.assertEqual(path.read_text().count("token_count"), 2)
        record = self.worth("report", "--since",
                            "2026-08-22T10:00:00.000Z")["cost"]["codex"]
        # cumulative 10 before the window, 15 inside: the window saw 5
        self.assertEqual(record.get("out"), 5)
        self.assertEqual(record.get("in"), 10)
        self.assertEqual(record.get("messages"), 1)
        waste = self.worth("waste", "--since",
                           "2026-08-22T10:00:00.000Z")
        heavy = [sig for sig in waste["signals"]
                 if sig.get("kind") == "heavy-turn"
                 and sig.get("harness") == "codex"]
        self.assertEqual(len(heavy), 1)
        # the heavy turn is the IN-WINDOW event with its own delta;
        # neither the pre-window stamp nor its cumulative may leak
        self.assertEqual(heavy[0].get("at"), "2026-08-22T10:30:00.000Z")
        self.assertEqual(heavy[0].get("out_delta"), 5)


class ClaudeReemissionAfterTheWindow(SeamCase):
    def test_a_later_reemission_does_not_erase_history(self):
        inside = self.claude_row(
            "m1", "2026-08-22T10:00:00.000Z", {"output_tokens": 7})
        after = self.claude_row(
            "m1", "2026-08-22T13:00:00.000Z", {"output_tokens": 900})
        path = self.write_lines(
            self.claude / self.slug() / "s.jsonl", [inside, after])
        self.assertEqual(path.read_text().count('"m1"'), 2)
        record = self.worth(
            "report", "--until", "2026-08-22T12:00:00.000Z",
        )["cost"]["claude"]
        self.assertEqual(record.get("messages"), 1)
        self.assertEqual(record.get("out"), 7)


class GrokIncompletenessIsWindowScoped(SeamCase):
    def test_an_incomplete_run_outside_the_window_says_nothing(self):
        early = int(datetime(2026, 8, 21, 9, 0,
                             tzinfo=timezone.utc).timestamp())
        late = int(datetime(2026, 8, 22, 10, 0,
                            tzinfo=timezone.utc).timestamp())
        self.grok_session("g", updates=[
            {"method": "session/update", "timestamp": early,
             "params": {"update": {"sessionUpdate": "turn_completed",
                                   "usage": {"totalTokens": 500,
                                             "outputTokens": 5,
                                             "usageIsIncomplete": True}}}},
            {"method": "session/update", "timestamp": late,
             "params": {"update": {"sessionUpdate": "turn_completed",
                                   "usage": {"totalTokens": 100,
                                             "outputTokens": 10,
                                             "costUsdTicks": 3}}}}])
        record = self.worth(
            "report", "--since", "2026-08-22T00:00:00.000Z",
        )["cost"]["grok"]
        self.assertEqual(record.get("runs"), 1)
        self.assertEqual(record.get("total"), 100)
        self.assertFalse(record.get("incomplete"))


class UnrecordedRecordsCarryNoZeroes(SeamCase):
    def test_the_unrecorded_shape_is_exactly_the_unrecorded_shape(self):
        data = self.worth("report")
        self.assertEqual(set(data["cost"]["claude"]),
                         {"sessions", "tokens"})
        self.assertEqual(set(data["cost"]["codex"]),
                         {"sessions", "tokens"})
        self.assertEqual(set(data["cost"]["grok"]),
                         {"sessions", "tokens"})

    def test_stamps_carry_exactly_their_documented_keys(self):
        report = self.worth("report")
        self.assertEqual(set(report["stamp"]),
                         {"head", "branch", "since", "until", "now"})
        waste = self.worth("waste")
        self.assertEqual(set(waste["stamp"]),
                         {"head", "branch", "since", "until", "now",
                          "sessions"})


class ResultsSideSeams(SeamGitCase):
    def test_a_binary_blob_in_an_edge_tree_is_not_a_crash(self):
        self.commit({"img.png": b"\x89PNG\x0d\x0a\x1a\x0a\xff\xfe",
                     "tests/t.rs": "#[test]\nfn a() {}\n"},
                    "binary and a test", "2026-08-22T09:00:00Z")
        data = self.worth("report")
        tests = data["results"]["tests"]
        self.assertEqual(tests.get("until"), 1)

    def test_python_definitions_count_too(self):
        self.commit({"tests/t.py": "def helper():\n    pass\n\n"
                                   "def test_one():\n    pass\n"},
                    "python test", "2026-08-22T09:00:00Z")
        data = self.worth("report")
        self.assertEqual(data["results"]["tests"].get("until"), 1)

    def test_helper_functions_are_not_test_definitions(self):
        self.commit({"tests/t.rs":
                     "fn helper() {}\n\n#[test]\nfn a() {}\n"},
                    "helper plus one test", "2026-08-22T09:00:00Z")
        data = self.worth("report")
        self.assertEqual(data["results"]["tests"].get("until"), 1)

    def test_edge_trees_honor_their_bounds_exactly(self):
        self.commit({"tests/t.rs": "#[test]\nfn a() {}\n"},
                    "one test", "2026-08-22T09:00:00Z")
        self.commit({"tests/t.rs": "#[test]\nfn a() {}\n#[test]\nfn b() {}\n"},
                    "two tests", "2026-08-22T10:00:00Z")
        data = self.worth("report", "--since", "2026-08-22T09:00:00.000Z",
                          "--until", "2026-08-22T10:00:00.000Z")
        tests = data["results"]["tests"]
        # since-edge is inclusive (the 09:00 commit), until-edge is
        # strict (the 10:00 commit is outside)
        self.assertEqual(tests.get("since"), 1)
        self.assertEqual(tests.get("until"), 1)
        self.assertEqual(tests.get("delta"), 0)

    def test_every_merge_entry_names_its_sha_and_subject(self):
        self.commit({"a.txt": "x\n"}, "base", "2026-08-22T09:00:00Z")
        subprocess.run(["git", "-C", str(self.repo), "switch", "-q",
                        "-c", "side"], check=True)
        self.commit({"b.txt": "y\n"}, "side work", "2026-08-22T09:30:00Z")
        subprocess.run(["git", "-C", str(self.repo), "switch", "-q",
                        "dev"], check=True)
        import os
        subprocess.run(["git", "-C", str(self.repo), "merge", "-q",
                        "--no-ff", "-m", "Merge pull request #7 from x/side",
                        "side"], check=True,
                       env={**os.environ,
                            "GIT_AUTHOR_DATE": "2026-08-22T10:30:00Z",
                            "GIT_COMMITTER_DATE": "2026-08-22T10:30:00Z",
                            "GIT_AUTHOR_NAME": "seam",
                            "GIT_AUTHOR_EMAIL": "s@x",
                            "GIT_COMMITTER_NAME": "seam",
                            "GIT_COMMITTER_EMAIL": "s@x"})
        data = self.worth("report")
        merges = data["results"]["merges"]
        self.assertEqual(len(merges), 1)
        self.assertEqual(set(merges[0]), {"pr", "sha", "subject"})
        self.assertEqual(merges[0]["pr"], 7)


if __name__ == "__main__":
    unittest.main()
