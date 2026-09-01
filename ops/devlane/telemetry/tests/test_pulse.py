"""Guards against stale inclusion, token miscounting, and leaks in pulse."""

import importlib.util
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
STORES = HERE.parents[2] / "fixtures" / "stores.py"

BASE_EPOCH = 1_787_306_400
NOW = BASE_EPOCH + 600
REPO = "/home/work/projects/minspec/workbench"
OTHER_REPO = "/tmp/other-minspec"
MARKER = "FIXTUREPROMPTMARKER"

CLAUDE_MODEL = "claude-fable-5"
CODEX_MODEL = "gpt-5-codex"
GROK_MODEL = "grok-4.6"

CLAUDE_TOKENS = {
    "input": 30,
    "cached": 12_000,
    "output": 500,
    "total": 12_530,
}
CLAUDE_REEMIT_GROWTH = {
    "input_tokens": 7,
    "cache_creation_input_tokens": 11,
    "cache_read_input_tokens": 13,
    "output_tokens": 17,
}
CLAUDE_GROWN_TOKENS = {
    "input": 37,
    "cached": 12_024,
    "output": 517,
    "total": 12_578,
}
CODEX_TOKENS = {
    "input": 400,
    "cached": 300,
    "output": 90,
    "total": 490,
}
PULSE_ROW_KEYS = {
    "harness",
    "session",
    "model",
    "age_seconds",
    "idle_seconds",
    "tokens",
    "recent",
}
TOKEN_KEYS = {"input", "cached", "output", "total"}
GROK_RECENT = [
    "phase_changed",
    "session/update",
    "search_code",
    "search_code",
    "permission_requested",
    "session/update",
    "permission_resolved",
    "loop_started",
    "phase_changed",
]


def load_module(testcase, path, name):
    testcase.assertTrue(
        path.is_file(),
        f"{path} is missing; live-session status has not been implemented",
    )
    spec = importlib.util.spec_from_file_location(name, path)
    testcase.assertIsNotNone(
        spec,
        f"{path} could not be given an import specification",
    )
    testcase.assertIsNotNone(
        spec.loader,
        f"{path} has no loader and cannot be exercised",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jsonl(path):
    return [
        json.loads(raw)
        for raw in path.read_text().splitlines()
        if raw.strip()
    ]


def slug(repo):
    return "-" + "-".join(repo.strip("/").split("/"))


class PulseCase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            PULSE.is_file(),
            f"{PULSE} is missing; every pulse contract must remain red",
        )
        self.stores = load_module(
            self,
            STORES,
            "minspec_fixture_stores_for_pulse",
        )
        self.temp = tempfile.TemporaryDirectory(prefix="pulse-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.claude_root = self.root / "claude"
        self.codex_root = self.root / "codex"
        self.grok_root = self.root / "grok"

    def build_three(self, suffix, repo=REPO, reemit_claude=False):
        claude_id = f"claude-{suffix}"
        codex_id = f"codex-{suffix}"
        grok_id = f"grok-{suffix}"

        self.stores.build_claude_store(
            self.claude_root,
            slug(repo),
            base_timestamp=BASE_EPOCH,
            cwd=repo,
            session_id=claude_id,
            model=CLAUDE_MODEL,
            effort="high",
            marker=MARKER,
            reemit_last=reemit_claude,
        )
        claude_stream = (
            self.claude_root / slug(repo) / f"{claude_id}.jsonl"
        )
        self.assertTrue(
            claude_stream.is_file(),
            f"{STORES}:build_claude_store did not create {claude_stream}",
        )

        before = set(
            self.codex_root.glob(
                "sessions/2026/08/21/rollout-*.jsonl"
            )
        )
        self.stores.build_codex_store(
            self.codex_root,
            base_timestamp=BASE_EPOCH,
            cwd=repo,
            session_id=codex_id,
            model=CODEX_MODEL,
            effort="high",
            marker=MARKER,
        )
        after = set(
            self.codex_root.glob(
                "sessions/2026/08/21/rollout-*.jsonl"
            )
        )
        created = after - before
        self.assertEqual(
            len(created),
            1,
            (
                f"{STORES}:build_codex_store created {len(created)} "
                f"new rollout files for session {codex_id}"
            ),
        )
        codex_stream = next(iter(created))

        self.stores.build_grok_store(
            self.grok_root,
            repo,
            base_timestamp=BASE_EPOCH,
            session_id=grok_id,
            model=GROK_MODEL,
            marker=MARKER,
        )
        grok_session = (
            self.grok_root
            / "sessions"
            / quote(repo, safe="")
            / grok_id
        )
        grok_summary = grok_session / "summary.json"
        grok_updates = grok_session / "updates.jsonl"
        grok_events = grok_session / "events.jsonl"
        for path in (grok_summary, grok_updates, grok_events):
            self.assertTrue(
                path.is_file(),
                f"{STORES}:build_grok_store did not create {path}",
            )

        return {
            "claude": {
                "id": claude_id,
                "model": CLAUDE_MODEL,
                "stream": claude_stream,
                "streams": [claude_stream],
                "raw": [claude_stream],
            },
            "codex": {
                "id": codex_id,
                "model": CODEX_MODEL,
                "stream": codex_stream,
                "streams": [codex_stream],
                "raw": [codex_stream],
            },
            "grok": {
                "id": grok_id,
                "model": GROK_MODEL,
                "summary": grok_summary,
                "updates": grok_updates,
                "events": grok_events,
                "streams": [grok_updates, grok_events],
                "non_streams": [grok_summary],
                "raw": [grok_summary, grok_updates, grok_events],
            },
        }

    def set_activity(self, record, timestamp):
        for path in record["streams"]:
            os.utime(path, (timestamp, timestamp))
            self.assertEqual(
                int(path.stat().st_mtime),
                timestamp,
                f"the planted activity mtime did not land on {path}",
            )
        for path in record.get("non_streams", []):
            old = BASE_EPOCH - 10_000
            os.utime(path, (old, old))
            self.assertEqual(
                int(path.stat().st_mtime),
                old,
                f"the planted non-stream mtime did not land on {path}",
            )

    def set_all_activity(self, records, offsets):
        for harness, offset in offsets.items():
            self.set_activity(records[harness], NOW - offset)

    def assert_marker_planted(self, records):
        claude_raw = records["claude"]["stream"].read_text()
        self.assertIn(
            MARKER,
            claude_raw,
            "the leak marker was not planted in Claude content",
        )

        codex_entries = jsonl(records["codex"]["stream"])
        codex_meta = [
            entry["payload"]
            for entry in codex_entries
            if entry.get("type") == "session_meta"
        ]
        self.assertEqual(
            len(codex_meta),
            1,
            (
                f"the Codex base-instructions plant lacks one "
                f"session_meta in {records['codex']['stream']}"
            ),
        )
        self.assertIn(
            MARKER,
            json.dumps(
                codex_meta[0]["base_instructions"],
                sort_keys=True,
            ),
            (
                f"the leak marker was not planted in Codex "
                f"base_instructions at {records['codex']['stream']}"
            ),
        )

        summary = json.loads(records["grok"]["summary"].read_text())
        for field in ("session_summary", "generated_title"):
            self.assertIn(
                MARKER,
                summary[field],
                (
                    f"the leak marker was not planted in Grok {field} "
                    f"at {records['grok']['summary']}"
                ),
            )

        updates = jsonl(records["grok"]["updates"])
        self.assertGreater(
            len(updates),
            0,
            "the Grok params plant found no updates",
        )
        for index, update in enumerate(updates):
            self.assertIn(
                MARKER,
                json.dumps(update["params"], sort_keys=True),
                (
                    f"the leak marker was not planted in Grok params "
                    f"{index} at {records['grok']['updates']}"
                ),
            )

    def grow_last_claude_reemit(self, record):
        stream = record["stream"]
        entries = jsonl(stream)
        self.assertGreater(
            len(entries),
            2,
            f"the grown re-emit plant found too few entries in {stream}",
        )

        reemit = entries[-1]
        message = reemit.get("message") or {}
        message_id = message.get("id")
        matching = [
            entry
            for entry in entries[:-1]
            if (entry.get("message") or {}).get("id") == message_id
        ]
        self.assertEqual(
            len(matching),
            1,
            (
                f"the grown re-emit plant expected one earlier occurrence "
                f"of {message_id!r} in {stream}"
            ),
        )
        original = matching[0]
        self.assertEqual(
            reemit,
            original,
            (
                f"the fixture's last Claude re-emit was not "
                f"byte-equivalent before the growth plant in {stream}"
            ),
        )

        original_usage = dict(original["message"]["usage"])
        self.assertEqual(
            set(original_usage),
            set(CLAUDE_REEMIT_GROWTH),
            (
                f"the grown re-emit plant found unexpected usage "
                f"currencies in {stream}"
            ),
        )
        grown_usage = {
            key: value + CLAUDE_REEMIT_GROWTH[key]
            for key, value in original_usage.items()
        }
        entries[-1]["message"]["usage"] = grown_usage
        stream.write_text(
            "".join(
                json.dumps(
                    entry,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
                for entry in entries
            )
        )

        planted = jsonl(stream)
        self.assertEqual(
            len(planted),
            len(entries),
            f"the grown re-emit plant changed the entry count in {stream}",
        )
        self.assertEqual(
            planted[-1]["sessionId"],
            record["id"],
            (
                f"the grown re-emit plant no longer resembles the "
                f"intended fixture in {stream}"
            ),
        )
        self.assertEqual(
            planted[-1]["message"]["id"],
            message_id,
            f"the grown re-emit plant changed the duplicate id in {stream}",
        )
        self.assertEqual(
            planted[-1]["message"]["usage"],
            grown_usage,
            f"the grown re-emit usage plant did not land in {stream}",
        )
        for key, original_value in original_usage.items():
            self.assertGreater(
                planted[-1]["message"]["usage"][key],
                original_value,
                (
                    f"the grown re-emit plant did not increase {key} "
                    f"in {stream}"
                ),
            )

    def assert_closed_json_shape(self, document):
        self.assertIsInstance(
            document,
            dict,
            f"{PULSE} --json did not emit an object",
        )
        self.assertEqual(
            set(document),
            {"sessions"},
            f"{PULSE} --json emitted top-level fields beyond sessions",
        )
        rows = document["sessions"]
        self.assertIsInstance(
            rows,
            list,
            f"{PULSE} --json sessions is not a list",
        )

        for index, row in enumerate(rows):
            self.assertIsInstance(
                row,
                dict,
                f"{PULSE} --json row {index} is not an object",
            )
            self.assertEqual(
                set(row),
                PULSE_ROW_KEYS,
                (
                    f"{PULSE} --json row {index} does not have exactly "
                    f"the closed status fields"
                ),
            )
            for key in ("harness", "session", "model"):
                self.assertIs(
                    type(row[key]),
                    str,
                    (
                        f"{PULSE} --json row {index} field {key} is not "
                        "a string"
                    ),
                )
            for key in ("age_seconds", "idle_seconds"):
                self.assertIs(
                    type(row[key]),
                    int,
                    (
                        f"{PULSE} --json row {index} field {key} is not "
                        "an integer"
                    ),
                )

            tokens = row["tokens"]
            if tokens is not None:
                self.assertIsInstance(
                    tokens,
                    dict,
                    f"{PULSE} --json row {index} tokens is not an object",
                )
                self.assertEqual(
                    set(tokens),
                    TOKEN_KEYS,
                    (
                        f"{PULSE} --json row {index} tokens has fields "
                        "outside the known token currencies"
                    ),
                )
                for key, value in tokens.items():
                    self.assertIs(
                        type(value),
                        int,
                        (
                            f"{PULSE} --json row {index} token {key} is "
                            "not an integer"
                        ),
                    )

            recent = row["recent"]
            self.assertIsInstance(
                recent,
                list,
                f"{PULSE} --json row {index} recent is not a list",
            )
            for name in recent:
                self.assertIs(
                    type(name),
                    str,
                    (
                        f"{PULSE} --json row {index} recent contains "
                        "a nested value"
                    ),
                )
                self.assertTrue(
                    name,
                    f"{PULSE} --json row {index} has an empty recent name",
                )
                self.assertEqual(
                    name,
                    name.strip(),
                    (
                        f"{PULSE} --json row {index} recent name has "
                        "surrounding whitespace"
                    ),
                )
                self.assertLessEqual(
                    len(name.encode()),
                    80,
                    (
                        f"{PULSE} --json row {index} recent name is not "
                        f"short: {name!r}"
                    ),
                )

    def run_pulse(self, *args):
        return subprocess.run(
            [
                sys.executable,
                str(PULSE),
                *args,
                "--now",
                str(NOW),
                "--claude-dir",
                str(self.claude_root),
                "--codex-dir",
                str(self.codex_root),
                "--grok-dir",
                str(self.grok_root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )


class LiveJsonStatus(PulseCase):
    def test_json_is_stable_sorted_last_wins_and_content_free(self):
        records = self.build_three("live", reemit_claude=True)
        self.grow_last_claude_reemit(records["claude"])
        self.set_all_activity(
            records,
            {"claude": 10, "codex": 20, "grok": 30},
        )
        self.assert_marker_planted(records)

        claude_entries = jsonl(records["claude"]["stream"])
        message_ids = [
            entry["message"]["id"]
            for entry in claude_entries
            if (entry.get("message") or {}).get("usage")
        ]
        self.assertGreater(
            len(message_ids),
            len(set(message_ids)),
            (
                f"the duplicate message-id plant did not land in "
                f"{records['claude']['stream']}"
            ),
        )

        naive_spend = 0
        keyed_spend = {}
        for entry in claude_entries:
            message = entry.get("message") or {}
            usage = message.get("usage")
            if not usage:
                continue
            spend = sum(usage.values())
            naive_spend += spend
            keyed_spend[message["id"]] = spend
        self.assertGreater(
            naive_spend,
            sum(keyed_spend.values()),
            (
                f"the grown Claude re-emit in "
                f"{records['claude']['stream']} cannot expose "
                "double-counting"
            ),
        )
        self.assertEqual(
            sum(keyed_spend.values()),
            CLAUDE_GROWN_TOKENS["total"],
            (
                f"the last-wins Claude spend plant in "
                f"{records['claude']['stream']} is wrong"
            ),
        )
        self.assertNotEqual(
            sum(keyed_spend.values()),
            CLAUDE_TOKENS["total"],
            (
                f"the grown Claude re-emit in "
                f"{records['claude']['stream']} cannot expose first-wins"
            ),
        )

        first = self.run_pulse(
            "--json",
            "--repo",
            REPO,
            "--tail",
            "20",
        )
        self.assertEqual(
            first.returncode,
            0,
            f"{PULSE} --json failed: {first.stderr}",
        )
        second = self.run_pulse(
            "--json",
            "--repo",
            REPO,
            "--tail",
            "20",
        )
        self.assertEqual(
            second.returncode,
            0,
            f"{PULSE} --json was not repeatable: {second.stderr}",
        )
        self.assertEqual(
            second.stdout,
            first.stdout,
            f"{PULSE} --json changed for identical stores and --now",
        )
        self.assertNotIn(
            MARKER,
            first.stdout + first.stderr,
            f"{PULSE} --json leaked planted session content",
        )

        document = json.loads(first.stdout)
        self.assert_closed_json_shape(document)
        rows = document["sessions"]
        self.assertEqual(
            len(rows),
            3,
            f"{PULSE} --json did not emit exactly three live sessions",
        )
        self.assertEqual(
            [(row["harness"], row["session"]) for row in rows],
            sorted(
                (row["harness"], row["session"]) for row in rows
            ),
            f"{PULSE} --json sessions are not sorted stably",
        )

        by_harness = {row["harness"]: row for row in rows}
        self.assertEqual(
            set(by_harness),
            {"claude", "codex", "grok"},
            f"{PULSE} --json omitted or duplicated a harness",
        )

        claude = by_harness["claude"]
        self.assertEqual(
            claude["session"],
            records["claude"]["id"],
            f"{PULSE} reported the wrong Claude session id",
        )
        self.assertEqual(
            claude["model"],
            CLAUDE_MODEL,
            f"{PULSE} reported the wrong Claude model",
        )
        self.assertEqual(
            claude["age_seconds"],
            600,
            f"{PULSE} computed Claude age from the wrong clock",
        )
        self.assertEqual(
            claude["idle_seconds"],
            10,
            f"{PULSE} did not compute Claude activity from stream mtime",
        )
        self.assertEqual(
            claude["tokens"],
            CLAUDE_GROWN_TOKENS,
            (
                f"{PULSE} did not count the grown Claude re-emit "
                "last-wins exactly once"
            ),
        )
        self.assertEqual(
            claude["recent"],
            ["Read", "Bash", "Bash"],
            (
                f"{PULSE} returned Claude content instead of the "
                "recent tool names"
            ),
        )

        codex = by_harness["codex"]
        self.assertEqual(
            codex["session"],
            records["codex"]["id"],
            (
                f"{PULSE} did not obtain the Codex session id from "
                "session_meta"
            ),
        )
        self.assertEqual(
            codex["model"],
            CODEX_MODEL,
            (
                f"{PULSE} did not merge the Codex model from "
                "turn_context"
            ),
        )
        self.assertEqual(
            codex["age_seconds"],
            600,
            f"{PULSE} computed Codex age from the wrong clock",
        )
        self.assertEqual(
            codex["idle_seconds"],
            20,
            f"{PULSE} did not compute Codex activity from rollout mtime",
        )
        self.assertEqual(
            codex["tokens"],
            CODEX_TOKENS,
            f"{PULSE} summed cumulative Codex token_count events",
        )
        self.assertEqual(
            codex["recent"],
            ["token_count", "shell_command", "token_count"],
            (
                f"{PULSE} returned Codex content instead of the "
                "recent event names"
            ),
        )

        grok = by_harness["grok"]
        self.assertEqual(
            grok["session"],
            records["grok"]["id"],
            f"{PULSE} reported the wrong Grok session id",
        )
        self.assertEqual(
            grok["model"],
            GROK_MODEL,
            f"{PULSE} reported the wrong Grok model",
        )
        self.assertEqual(
            grok["age_seconds"],
            600,
            f"{PULSE} computed Grok age from the wrong clock",
        )
        self.assertEqual(
            grok["idle_seconds"],
            30,
            f"{PULSE} did not compute Grok activity from stream mtimes",
        )
        self.assertIsNone(
            grok["tokens"],
            f"{PULSE} invented a Grok token measurement",
        )
        self.assertEqual(
            grok["recent"],
            GROK_RECENT,
            (
                f"{PULSE} did not merge Grok update methods and "
                "event names by their native timestamps"
            ),
        )

    def test_byte_identical_claude_reemit_is_counted_once(self):
        records = self.build_three(
            "identical",
            reemit_claude=True,
        )
        self.set_all_activity(
            records,
            {"claude": 10, "codex": 20, "grok": 30},
        )

        stream = records["claude"]["stream"]
        raw_lines = [
            line for line in stream.read_text().splitlines() if line
        ]
        self.assertGreater(
            len(raw_lines),
            2,
            f"the byte-identical re-emit plant found too few lines in {stream}",
        )
        self.assertIn(
            raw_lines[-1],
            raw_lines[:-1],
            (
                f"the final Claude record in {stream} is not a "
                "byte-identical re-emit"
            ),
        )

        proc = self.run_pulse(
            "--json",
            "--repo",
            REPO,
            "--tail",
            "20",
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"{PULSE} failed on a byte-identical re-emit: {proc.stderr}",
        )
        rows = json.loads(proc.stdout)["sessions"]
        claude = next(
            row for row in rows if row["harness"] == "claude"
        )
        self.assertEqual(
            claude["tokens"],
            CLAUDE_TOKENS,
            (
                f"{PULSE} did not count a byte-identical Claude "
                "re-emit exactly once"
            ),
        )


class CompactPlainStatus(PulseCase):
    def test_plain_lines_are_complete_bounded_and_name_only(self):
        records = self.build_three("plain")
        self.set_all_activity(
            records,
            {"claude": 11, "codex": 22, "grok": 33},
        )
        self.assert_marker_planted(records)

        proc = self.run_pulse(
            "--repo",
            REPO,
            "--tail",
            "2",
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"{PULSE} plain output failed: {proc.stderr}",
        )
        self.assertNotIn(
            MARKER,
            proc.stdout + proc.stderr,
            f"{PULSE} plain output leaked planted session content",
        )

        lines = [
            line for line in proc.stdout.splitlines() if line.strip()
        ]
        self.assertEqual(
            len(lines),
            3,
            f"{PULSE} must print one compact line per live session",
        )
        for line in lines:
            self.assertLessEqual(
                len(line.encode()),
                320,
                f"{PULSE} exceeded the per-session token diet: {line}",
            )

        by_harness = {
            line.split(maxsplit=1)[0]: line for line in lines
        }
        self.assertEqual(
            set(by_harness),
            {"claude", "codex", "grok"},
            f"{PULSE} plain output omitted or duplicated a harness",
        )

        expected_common = {
            "claude": (
                records["claude"]["id"],
                CLAUDE_MODEL,
                "idle=11s",
            ),
            "codex": (
                records["codex"]["id"],
                CODEX_MODEL,
                "idle=22s",
            ),
            "grok": (
                records["grok"]["id"],
                GROK_MODEL,
                "idle=33s",
            ),
        }
        for harness, values in expected_common.items():
            line = by_harness[harness]
            for value in values:
                self.assertIn(
                    value,
                    line,
                    (
                        f"{PULSE} {harness} line omitted required "
                        f"status value {value!r}"
                    ),
                )
            self.assertIn(
                "age=600s",
                line,
                f"{PULSE} {harness} line has the wrong session age",
            )

        self.assertIn(
            "tokens=12530",
            by_harness["claude"],
            f"{PULSE} plain Claude spend is wrong",
        )
        self.assertIn(
            "recent=Read,Bash",
            by_harness["claude"],
            f"{PULSE} plain Claude tail is not the last two tool names",
        )
        self.assertIn(
            "tokens=490",
            by_harness["codex"],
            f"{PULSE} plain Codex spend is not the last cumulative count",
        )
        self.assertIn(
            "recent=shell_command,token_count",
            by_harness["codex"],
            f"{PULSE} plain Codex tail is not the last two event names",
        )
        self.assertEqual(
            by_harness["grok"].count("tokens=unrecorded"),
            1,
            f"{PULSE} must state the Grok token gap exactly once",
        )
        self.assertNotIn(
            "tokens=0",
            by_harness["grok"],
            f"{PULSE} rendered unrecorded Grok tokens as zero",
        )
        self.assertIn(
            "recent=loop_started,phase_changed",
            by_harness["grok"],
            (
                f"{PULSE} plain Grok tail did not use event types "
                "when tool_name was absent"
            ),
        )


class LivenessAndFiltering(PulseCase):
    def test_default_window_includes_boundary_and_excludes_dead_or_other_repo(self):
        boundary = self.build_three("boundary", REPO)
        dead = self.build_three("dead", REPO)
        other = self.build_three("other", OTHER_REPO)

        for record in boundary.values():
            self.set_activity(record, NOW - 300)
        for record in dead.values():
            self.set_activity(record, NOW - 301)
        for record in other.values():
            self.set_activity(record, NOW - 1)

        proc = self.run_pulse("--json", "--repo", REPO)
        self.assertEqual(
            proc.returncode,
            0,
            f"{PULSE} failed while applying liveness filters: {proc.stderr}",
        )
        document = json.loads(proc.stdout)
        self.assert_closed_json_shape(document)
        rows = document["sessions"]
        found = [
            (row["harness"], row["session"]) for row in rows
        ]
        expected = sorted(
            (
                harness,
                boundary[harness]["id"],
            )
            for harness in ("claude", "codex", "grok")
        )
        self.assertEqual(
            found,
            expected,
            (
                f"{PULSE} default 300-second window or --repo filter "
                "included the wrong sessions"
            ),
        )

    def test_live_window_option_uses_the_injected_clock_at_its_boundary(self):
        records = self.build_three("window")
        for record in records.values():
            self.set_activity(record, NOW - 600)

        included = self.run_pulse(
            "--json",
            "--repo",
            REPO,
            "--live-window",
            "600",
        )
        self.assertEqual(
            included.returncode,
            0,
            f"{PULSE} rejected --live-window 600: {included.stderr}",
        )
        included_document = json.loads(included.stdout)
        self.assert_closed_json_shape(included_document)
        self.assertEqual(
            len(included_document["sessions"]),
            3,
            (
                f"{PULSE} excluded streams exactly on the injected "
                "live-window boundary"
            ),
        )

        excluded = self.run_pulse(
            "--json",
            "--repo",
            REPO,
            "--live-window",
            "599",
        )
        self.assertEqual(
            excluded.returncode,
            0,
            f"{PULSE} rejected --live-window 599: {excluded.stderr}",
        )
        excluded_document = json.loads(excluded.stdout)
        self.assert_closed_json_shape(excluded_document)
        self.assertEqual(
            excluded_document,
            {"sessions": []},
            (
                f"{PULSE} included streams older than the injected "
                "live-window"
            ),
        )

    def test_grok_liveness_is_the_freshest_stream_not_all_or_one(self):
        """updates and events diverge in life; the session is live if
        EITHER is fresh, and idle is the freshest stream's age — a
        max()-over-streams reader and an events-only reader both die
        on one of the two splits."""
        records = self.build_three("split")
        grok = records["grok"]
        for stale, fresh, name in (
            (grok["updates"], grok["events"], "updates-stale"),
            (grok["events"], grok["updates"], "events-stale"),
        ):
            with self.subTest(split=name):
                os.utime(stale, (NOW - 400, NOW - 400))
                os.utime(fresh, (NOW - 10, NOW - 10))
                self.assertEqual(
                    int(stale.stat().st_mtime),
                    NOW - 400,
                    f"the stale mtime plant did not land on {stale}",
                )
                self.assertEqual(
                    int(fresh.stat().st_mtime),
                    NOW - 10,
                    f"the fresh mtime plant did not land on {fresh}",
                )
                document = self.invoke_grok_only()
                rows = [
                    row
                    for row in document["sessions"]
                    if row["harness"] == "grok"
                ]
                self.assertEqual(
                    [row["session"] for row in rows],
                    [grok["id"]],
                    f"{PULSE} declared the session dead on the {name}"
                    " split even though one stream is fresh",
                )
                self.assertEqual(
                    rows[0]["idle_seconds"],
                    10,
                    f"{PULSE} did not take idle from the freshest"
                    f" stream on the {name} split",
                )

    def invoke_grok_only(self):
        proc = self.run_pulse("--json", "--repo", REPO)
        self.assertEqual(
            proc.returncode,
            0,
            f"{PULSE} failed on the divergent-mtime build: {proc.stderr}",
        )
        return json.loads(proc.stdout)

    def test_no_live_sessions_has_exact_empty_outputs_and_exit_zero(self):
        records = self.build_three("stale")
        for record in records.values():
            self.set_activity(record, NOW - 301)

        plain = self.run_pulse("--repo", REPO)
        self.assertEqual(
            plain.returncode,
            0,
            f"{PULSE} plain empty status failed: {plain.stderr}",
        )
        self.assertEqual(
            plain.stdout.strip(),
            "no live sessions",
            f"{PULSE} plain empty status has the wrong message",
        )

        structured = self.run_pulse("--json", "--repo", REPO)
        self.assertEqual(
            structured.returncode,
            0,
            f"{PULSE} JSON empty status failed: {structured.stderr}",
        )
        self.assertEqual(
            structured.stdout.strip(),
            '{"sessions": []}',
            f"{PULSE} JSON empty status is not the required stable object",
        )
        self.assert_closed_json_shape(json.loads(structured.stdout))


if __name__ == "__main__":
    unittest.main()
