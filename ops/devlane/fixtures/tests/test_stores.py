"""Guards against nondeterministic, malformed, or content-leaking fixtures."""

import argparse
import importlib.util
import inspect
import itertools
import json
import re
import subprocess
import sys
import tempfile
import typing
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve()
STORES = HERE.parents[1] / "stores.py"
USAGE = HERE.parents[2] / "telemetry" / "usage.py"
BREAKER = HERE.parents[2] / "telemetry" / "breaker.py"

BASE_EPOCH = 1_787_306_400
SECOND_BASE_EPOCH = BASE_EPOCH + 86_400
BASE_ISO = "2026-08-21T10:00:00.000Z"
SECOND_BASE_ISO = "2026-08-22T10:00:00.000Z"
CLAUDE_END = "2026-08-21T10:05:00.000Z"
CODEX_END = "2026-08-21T10:09:00.000Z"
GROK_BASE = "2026-08-21T10:00:00.000000000Z"
SECOND_GROK_BASE = "2026-08-22T10:00:00.000000000Z"
GROK_END = "2026-08-21T10:20:00.000000000Z"

REPO = "/home/work/projects/minspec/workbench"
SLUG = "-home-work-projects-minspec-workbench"
MARKER = "FIXTUREPROMPTMARKER"

CLAUDE_SESSION = "claude-fixture"
CODEX_SESSION = "codex-fixture"
GROK_SESSION = "grok-fixture"

CLAUDE_MODEL = "claude-fable-5"
CODEX_MODEL = "gpt-5-codex"
GROK_MODEL = "grok-4.6"

CLAUDE_TOKENS = {
    "input": 30,
    "cached": 12_000,
    "output": 500,
    "total": 12_530,
}
CODEX_TOKENS = {
    "input": 400,
    "cached": 300,
    "output": 90,
    "total": 490,
}
CODEX_RAW_TOKENS = {
    "input_tokens": 400,
    "cached_input_tokens": 300,
    "output_tokens": 90,
    "reasoning_output_tokens": 30,
    "total_tokens": 490,
}
GROK_EVENT_TYPES = [
    "phase_changed",
    "tool_started",
    "tool_completed",
    "permission_requested",
    "permission_resolved",
    "loop_started",
    "phase_changed",
]
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
        f"{path} is missing; the contract requires this module to exist",
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


def native_epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    from datetime import datetime

    text = re.sub(r"\.(\d{6})\d+", r".\1", str(value).replace("Z", "+00:00"))
    return datetime.fromisoformat(text).timestamp()


def jsonl(path):
    return [
        json.loads(raw)
        for raw in path.read_text().splitlines()
        if raw.strip()
    ]


def only_path(testcase, paths, location):
    paths = list(paths)
    testcase.assertEqual(
        len(paths),
        1,
        f"expected exactly one fixture artifact at {location}, found {paths}",
    )
    return paths[0]


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def nested_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from nested_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_keys(child)


class StoreBuilderCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stores-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def stores(self):
        return load_module(self, STORES, "minspec_fixture_stores")

    def build_all(self, root, base_timestamp=BASE_EPOCH):
        stores = self.stores()
        claude_root = root / "claude"
        codex_root = root / "codex"
        grok_root = root / "grok"

        stores.build_claude_store(
            claude_root,
            SLUG,
            base_timestamp=base_timestamp,
            cwd=REPO,
            session_id=CLAUDE_SESSION,
            model=CLAUDE_MODEL,
            effort="high",
            marker=MARKER,
        )
        stores.build_codex_store(
            codex_root,
            base_timestamp=base_timestamp,
            cwd=REPO,
            session_id=CODEX_SESSION,
            model=CODEX_MODEL,
            effort="high",
            marker=MARKER,
        )
        stores.build_grok_store(
            grok_root,
            REPO,
            base_timestamp=base_timestamp,
            session_id=GROK_SESSION,
            model=GROK_MODEL,
            marker=MARKER,
        )

        claude_stream = claude_root / SLUG / f"{CLAUDE_SESSION}.jsonl"
        codex_stream = only_path(
            self,
            codex_root.glob("sessions/*/*/*/rollout-*.jsonl"),
            codex_root / "sessions",
        )
        grok_session = (
            grok_root
            / "sessions"
            / quote(REPO, safe="")
            / GROK_SESSION
        )

        return {
            "claude_root": claude_root,
            "claude_stream": claude_stream,
            "codex_root": codex_root,
            "codex_stream": codex_stream,
            "grok_root": grok_root,
            "grok_session": grok_session,
        }


class ExplicitTimeAndDeterminism(StoreBuilderCase):
    def test_every_builder_requires_and_obeys_two_base_timestamps(self):
        stores = self.stores()
        for name in (
            "build_claude_store",
            "build_codex_store",
            "build_grok_store",
        ):
            with self.subTest(builder=name):
                builder = getattr(stores, name, None)
                self.assertIsNotNone(
                    builder,
                    f"{STORES}:{name} is missing",
                )
                parameter = inspect.signature(builder).parameters.get(
                    "base_timestamp"
                )
                self.assertIsNotNone(
                    parameter,
                    (
                        f"{STORES}:{name} has no explicit "
                        "base_timestamp parameter"
                    ),
                )
                self.assertIs(
                    parameter.default,
                    inspect.Parameter.empty,
                    (
                        f"{STORES}:{name} permits an implicit clock; "
                        "base_timestamp must be required"
                    ),
                )

        first = self.build_all(
            self.root / "first",
            base_timestamp=BASE_EPOCH,
        )
        second = self.build_all(
            self.root / "second",
            base_timestamp=SECOND_BASE_EPOCH,
        )

        measured = []
        for label, artifacts, expected_iso, expected_grok in (
            ("first", first, BASE_ISO, GROK_BASE),
            (
                "second",
                second,
                SECOND_BASE_ISO,
                SECOND_GROK_BASE,
            ),
        ):
            claude = jsonl(artifacts["claude_stream"])
            codex = jsonl(artifacts["codex_stream"])
            summary = json.loads(
                (artifacts["grok_session"] / "summary.json").read_text()
            )

            self.assertGreater(
                len(claude),
                0,
                f"{artifacts['claude_stream']} contains no entries",
            )
            self.assertGreater(
                len(codex),
                0,
                f"{artifacts['codex_stream']} contains no entries",
            )
            self.assertEqual(
                claude[0]["timestamp"],
                expected_iso,
                (
                    f"{artifacts['claude_stream']} ignored the {label} "
                    "base_timestamp"
                ),
            )
            self.assertEqual(
                codex[0]["timestamp"],
                expected_iso,
                (
                    f"{artifacts['codex_stream']} ignored the {label} "
                    "base_timestamp"
                ),
            )
            self.assertEqual(
                summary["created_at"],
                expected_grok,
                (
                    f"{artifacts['grok_session'] / 'summary.json'} "
                    f"ignored the {label} base_timestamp"
                ),
            )
            measured.append(
                (
                    claude[0]["timestamp"],
                    codex[0]["timestamp"],
                    summary["created_at"],
                )
            )

        self.assertNotEqual(
            measured[0],
            measured[1],
            (
                "the two distinct base_timestamp plants produced the "
                "same fixture timestamps"
            ),
        )

    def test_repeated_builds_with_identical_arguments_are_byte_identical(self):
        self.build_all(self.root)
        first = snapshot(self.root)
        self.assertGreater(
            len(first),
            0,
            f"{self.root} remained empty after the first fixture build",
        )

        self.build_all(self.root)
        second = snapshot(self.root)
        self.assertEqual(
            second,
            first,
            (
                f"rebuilding fixtures under {self.root} changed their "
                "paths or bytes"
            ),
        )


class MeasuredStoreShapes(StoreBuilderCase):
    def test_claude_entries_match_the_measured_message_shape(self):
        artifacts = self.build_all(self.root)
        entries = jsonl(artifacts["claude_stream"])
        self.assertEqual(
            len(entries),
            2,
            f"{artifacts['claude_stream']} has the wrong message count",
        )

        entry_keys = {
            "timestamp",
            "cwd",
            "gitBranch",
            "effort",
            "isSidechain",
            "sessionId",
            "message",
        }
        message_keys = {"id", "model", "usage", "content"}
        usage_keys = {
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        }

        for index, entry in enumerate(entries):
            self.assertTrue(
                entry_keys <= entry.keys(),
                (
                    f"{artifacts['claude_stream']} entry {index} lacks "
                    f"measured keys: {entry_keys - entry.keys()}"
                ),
            )
            self.assertEqual(
                entry["cwd"],
                REPO,
                f"{artifacts['claude_stream']} entry {index} lost cwd",
            )
            self.assertEqual(
                entry["sessionId"],
                CLAUDE_SESSION,
                (
                    f"{artifacts['claude_stream']} entry {index} "
                    "lost sessionId"
                ),
            )
            message = entry["message"]
            self.assertTrue(
                message_keys <= message.keys(),
                (
                    f"{artifacts['claude_stream']} message {index} lacks "
                    f"measured keys: {message_keys - message.keys()}"
                ),
            )
            self.assertEqual(
                set(message["usage"]),
                usage_keys,
                (
                    f"{artifacts['claude_stream']} message {index} "
                    "has the wrong usage currencies"
                ),
            )

        blocks = [
            block
            for entry in entries
            for block in entry["message"]["content"]
            if isinstance(block, dict)
        ]
        self.assertGreater(
            len(blocks),
            0,
            f"{artifacts['claude_stream']} contains no content blocks",
        )
        tool_uses = [
            block for block in blocks if block.get("type") == "tool_use"
        ]
        tool_results = [
            block for block in blocks if block.get("type") == "tool_result"
        ]
        self.assertGreater(
            len(tool_uses),
            0,
            f"{artifacts['claude_stream']} contains no tool_use block",
        )
        self.assertGreater(
            len(tool_results),
            0,
            f"{artifacts['claude_stream']} contains no tool_result block",
        )
        self.assertTrue(
            all({"name", "input"} <= block.keys() for block in tool_uses),
            (
                f"{artifacts['claude_stream']} has a tool_use block "
                "without name and input"
            ),
        )
        self.assertTrue(
            all("is_error" in block for block in tool_results),
            (
                f"{artifacts['claude_stream']} has a tool_result block "
                "without is_error"
            ),
        )

    def test_codex_store_splits_meta_context_and_cumulative_counts(self):
        artifacts = self.build_all(self.root)
        entries = jsonl(artifacts["codex_stream"])
        self.assertEqual(
            len(entries),
            5,
            f"{artifacts['codex_stream']} has the wrong event count",
        )

        meta = [
            entry
            for entry in entries
            if entry.get("type") == "session_meta"
        ]
        context = [
            entry
            for entry in entries
            if entry.get("type") == "turn_context"
        ]
        self.assertEqual(
            len(meta),
            1,
            f"{artifacts['codex_stream']} must contain one session_meta",
        )
        self.assertEqual(
            len(context),
            1,
            f"{artifacts['codex_stream']} must contain one turn_context",
        )

        meta_payload = meta[0]["payload"]
        self.assertTrue(
            {"id", "cwd", "base_instructions"} <= meta_payload.keys(),
            (
                f"{artifacts['codex_stream']} session_meta lacks id, cwd, "
                "or base_instructions"
            ),
        )
        self.assertNotIn(
            "model",
            meta_payload,
            (
                f"{artifacts['codex_stream']} put model in session_meta "
                "instead of turn_context"
            ),
        )
        self.assertNotIn(
            "effort",
            meta_payload,
            (
                f"{artifacts['codex_stream']} put effort in session_meta "
                "instead of turn_context"
            ),
        )
        self.assertEqual(
            meta_payload["id"],
            CODEX_SESSION,
            f"{artifacts['codex_stream']} session_meta has the wrong id",
        )
        self.assertEqual(
            meta_payload["cwd"],
            REPO,
            f"{artifacts['codex_stream']} session_meta has the wrong cwd",
        )
        base_instructions = meta_payload["base_instructions"]
        self.assertIsInstance(
            base_instructions,
            dict,
            (
                f"{artifacts['codex_stream']} base_instructions is not "
                "the measured object shape"
            ),
        )
        self.assertEqual(
            set(base_instructions),
            {"text"},
            (
                f"{artifacts['codex_stream']} base_instructions has "
                "unexpected fixture fields"
            ),
        )
        self.assertIn(
            MARKER,
            base_instructions["text"],
            (
                f"{artifacts['codex_stream']} did not plant content in "
                "base_instructions.text"
            ),
        )

        context_payload = context[0]["payload"]
        self.assertTrue(
            {"model", "effort", "cwd"} <= context_payload.keys(),
            (
                f"{artifacts['codex_stream']} turn_context lacks model, "
                "effort, or cwd"
            ),
        )
        self.assertNotIn(
            "id",
            context_payload,
            (
                f"{artifacts['codex_stream']} put the session id in "
                "turn_context instead of session_meta"
            ),
        )
        self.assertEqual(
            context_payload["model"],
            CODEX_MODEL,
            f"{artifacts['codex_stream']} turn_context has the wrong model",
        )
        self.assertEqual(
            context_payload["effort"],
            "high",
            f"{artifacts['codex_stream']} turn_context has the wrong effort",
        )
        self.assertEqual(
            context_payload["cwd"],
            REPO,
            f"{artifacts['codex_stream']} turn_context has the wrong cwd",
        )

        counts = [
            entry["payload"]["info"]["total_token_usage"]
            for entry in entries
            if (entry.get("payload") or {}).get("type") == "token_count"
        ]
        self.assertEqual(
            len(counts),
            2,
            (
                f"{artifacts['codex_stream']} must contain two cumulative "
                "token_count events"
            ),
        )
        for index, count in enumerate(counts):
            self.assertEqual(
                set(count),
                set(CODEX_RAW_TOKENS),
                (
                    f"{artifacts['codex_stream']} token_count {index} "
                    "has the wrong currencies"
                ),
            )
        self.assertEqual(
            counts[-1],
            CODEX_RAW_TOKENS,
            (
                f"{artifacts['codex_stream']} last cumulative token_count "
                "is not the expected spend"
            ),
        )
        self.assertLess(
            counts[0]["total_tokens"],
            counts[-1]["total_tokens"],
            (
                f"{artifacts['codex_stream']} does not demonstrate "
                "cumulative growth"
            ),
        )

    def test_grok_store_matches_measured_content_and_activity_shapes(self):
        artifacts = self.build_all(self.root)
        session = artifacts["grok_session"]
        summary_path = session / "summary.json"
        updates_path = session / "updates.jsonl"
        events_path = session / "events.jsonl"

        summary = json.loads(summary_path.read_text())
        self.assertTrue(
            {
                "created_at",
                "updated_at",
                "num_messages",
                "current_model_id",
                "session_summary",
                "generated_title",
                "reasoning_effort",
            }
            <= summary.keys(),
            f"{summary_path} lacks the measured Grok summary keys",
        )
        self.assertEqual(
            summary["info"]["id"],
            GROK_SESSION,
            f"{summary_path} has the wrong session id",
        )
        self.assertEqual(
            summary["info"]["cwd"],
            REPO,
            f"{summary_path} has the wrong cwd",
        )
        self.assertEqual(
            summary["reasoning_effort"],
            "high",
            f"{summary_path} has the wrong reasoning_effort",
        )
        for field in ("session_summary", "generated_title"):
            self.assertIn(
                MARKER,
                summary[field],
                f"{summary_path} did not plant content in {field}",
            )

        token_keys = [
            key for key in nested_keys(summary) if "token" in key.lower()
        ]
        self.assertEqual(
            token_keys,
            [],
            (
                f"{summary_path} invented token usage keys even though "
                f"Grok records none: {token_keys}"
            ),
        )

        updates = jsonl(updates_path)
        events = jsonl(events_path)
        self.assertEqual(
            len(updates),
            2,
            f"{updates_path} does not demonstrate activity growth",
        )
        self.assertEqual(
            len(events),
            len(GROK_EVENT_TYPES),
            f"{events_path} has the wrong measured event set",
        )

        for index, update in enumerate(updates):
            self.assertEqual(
                set(update),
                {"method", "params", "timestamp"},
                (
                    f"{updates_path} update {index} does not have exactly "
                    "method, params, and timestamp"
                ),
            )
            self.assertEqual(
                update["method"],
                "session/update",
                f"{updates_path} update {index} has the wrong method",
            )
            self.assertIsInstance(
                update["params"],
                dict,
                f"{updates_path} update {index} params is not an object",
            )
            self.assertIn(
                MARKER,
                json.dumps(update["params"], sort_keys=True),
                f"{updates_path} update {index} lacks the params marker",
            )
            self.assertNotIn(
                "name",
                update,
                f"{updates_path} update {index} invented a name field",
            )
            self.assertIs(
                type(update["timestamp"]),
                int,
                (
                    f"{updates_path} update {index} timestamp is not the"
                    " measured epoch-integer type (live: 28448/28448 int)"
                ),
            )

        self.assertLess(
            updates[0]["timestamp"],
            updates[-1]["timestamp"],
            f"{updates_path} activity timestamps do not advance",
        )

        event_types = [event["type"] for event in events]
        self.assertEqual(
            event_types,
            GROK_EVENT_TYPES,
            f"{events_path} has the wrong measured event types",
        )
        self.assertGreater(
            event_types.count("phase_changed"),
            max(
                event_types.count(event_type)
                for event_type in set(event_types)
                if event_type != "phase_changed"
            ),
            f"{events_path} does not make phase_changed dominant",
        )

        for index, event in enumerate(events):
            expected_keys = {"type", "ts"}
            if event["type"] in {"tool_started", "tool_completed"}:
                expected_keys.add("tool_name")
                self.assertEqual(
                    event["tool_name"],
                    "search_code",
                    (
                        f"{events_path} tool event {index} has the wrong "
                        "tool_name"
                    ),
                )
            self.assertEqual(
                set(event),
                expected_keys,
                (
                    f"{events_path} event {index} has fields outside the "
                    "measured shape"
                ),
            )
            self.assertNotIn(
                "name",
                event,
                f"{events_path} event {index} invented a name field",
            )
            self.assertIs(
                type(event["ts"]),
                str,
                (
                    f"{events_path} event {index} ts is not the measured"
                    " ISO-string type (live: 3836/3836 str)"
                ),
            )

        for first, second in itertools.pairwise(events):
            self.assertLess(
                first["ts"],
                second["ts"],
                f"{events_path} activity timestamps do not advance",
            )

        activity = [
            (update["timestamp"], update["method"])
            for update in updates
        ]
        activity.extend(
            (
                event["ts"],
                event.get("tool_name", event["type"]),
            )
            for event in events
        )
        self.assertEqual(
            [name for _, name in sorted(
                activity, key=lambda pair: native_epoch(pair[0]))],
            GROK_RECENT,
            (
                f"{updates_path} and {events_path} do not plant the "
                "required native-timestamp merge order"
            ),
        )


class ExistingReadersRoundTripTheFixtures(StoreBuilderCase):
    def test_usage_readers_return_exact_sessions_counts_and_grok_gap(self):
        artifacts = self.build_all(self.root)
        usage = load_module(self, USAGE, "minspec_usage_for_fixture_test")

        claude = list(
            usage.claude_sessions(artifacts["claude_root"], REPO)
        )
        codex = list(usage.codex_sessions(artifacts["codex_root"], REPO))
        grok = list(usage.grok_sessions(artifacts["grok_root"], REPO))

        self.assertEqual(
            claude,
            [
                {
                    "harness": "claude",
                    "session": CLAUDE_SESSION,
                    "model": CLAUDE_MODEL,
                    "started": BASE_ISO,
                    "ended": CLAUDE_END,
                    "messages": 2,
                    "tokens": CLAUDE_TOKENS,
                }
            ],
            (
                f"{USAGE}:claude_sessions did not round-trip the "
                "built Claude store"
            ),
        )
        self.assertEqual(
            codex,
            [
                {
                    "harness": "codex",
                    "session": CODEX_SESSION,
                    "model": CODEX_MODEL,
                    "started": BASE_ISO,
                    "ended": CODEX_END,
                    "messages": 5,
                    "tokens": CODEX_TOKENS,
                }
            ],
            (
                f"{USAGE}:codex_sessions did not merge session_meta with "
                "turn_context or use the last cumulative token_count"
            ),
        )
        self.assertEqual(
            grok,
            [
                {
                    "harness": "grok",
                    "session": GROK_SESSION,
                    "model": GROK_MODEL,
                    "started": GROK_BASE,
                    "ended": GROK_END,
                    "messages": 3,
                    "tokens": None,
                    "incomplete": False,
                    "reasoning": None,
                    "cost_usd_ticks": None,
                    "note": usage.GROK_GAP,
                }
            ],
            (
                f"{USAGE}:grok_sessions did not preserve the "
                "unrecorded-token gap"
            ),
        )

    def test_planted_content_is_raw_but_never_in_reader_aggregates(self):
        artifacts = self.build_all(self.root)
        codex_entries = jsonl(artifacts["codex_stream"])
        codex_meta = [
            entry["payload"]
            for entry in codex_entries
            if entry.get("type") == "session_meta"
        ]
        self.assertEqual(
            len(codex_meta),
            1,
            (
                f"{artifacts['codex_stream']} lacks the planted "
                "session_meta"
            ),
        )

        grok_summary = json.loads(
            (artifacts["grok_session"] / "summary.json").read_text()
        )
        grok_updates = jsonl(
            artifacts["grok_session"] / "updates.jsonl"
        )

        planted_channels = {
            "Claude content": artifacts["claude_stream"].read_text(),
            "Codex base_instructions": json.dumps(
                codex_meta[0]["base_instructions"],
                sort_keys=True,
            ),
            "Grok session_summary": grok_summary["session_summary"],
            "Grok generated_title": grok_summary["generated_title"],
        }
        for index, update in enumerate(grok_updates):
            planted_channels[f"Grok params {index}"] = json.dumps(
                update["params"],
                sort_keys=True,
            )

        for channel, raw in planted_channels.items():
            self.assertIn(
                MARKER,
                raw,
                f"the leak marker was not planted in {channel}",
            )

        usage = load_module(self, USAGE, "minspec_usage_for_leak_test")
        args = argparse.Namespace(
            claude_dir=str(artifacts["claude_root"]),
            codex_dir=str(artifacts["codex_root"]),
            grok_dir=str(artifacts["grok_root"]),
            repo=REPO,
        )
        aggregate = json.dumps(usage.collect(args), sort_keys=True)
        self.assertNotIn(
            MARKER,
            aggregate,
            f"{USAGE}:collect leaked raw session content",
        )


class ReadersRefuseSnapshotRewriteInflation(StoreBuilderCase):
    """Live Claude streams re-emit message ids (measured 2026-08-21:
    2086 usage lines over 1052 unique ids in one session). A reader
    that sums lines nearly doubles the spend; one that keeps the first
    copy misses growth. Only last-wins-by-id survives both plants."""

    GROWTH: typing.ClassVar[dict] = {"input_tokens": 7, "cache_creation_input_tokens": 11,
              "cache_read_input_tokens": 13, "output_tokens": 17}

    def reemitted_stream(self, grown):
        stores = self.stores()
        stores.build_claude_store(
            self.root / "claude", SLUG, base_timestamp=BASE_EPOCH,
            cwd=REPO, session_id=CLAUDE_SESSION, model=CLAUDE_MODEL,
            effort="high", marker=MARKER, reemit_last=True)
        stream = self.root / "claude" / SLUG / f"{CLAUDE_SESSION}.jsonl"
        entries = jsonl(stream)
        ids = [e["message"]["id"] for e in entries if e["message"].get("usage")]
        self.assertGreater(len(ids), len(set(ids)),
                           f"the duplicate-id plant did not land in {stream}")
        if grown:
            last = entries[-1]
            self.assertEqual(last, entries[-2],
                             f"the re-emit in {stream} is not byte-equivalent"
                             " before the growth plant")
            for key, bump in self.GROWTH.items():
                last["message"]["usage"][key] += bump
            stream.write_text("".join(json.dumps(e) + "\n" for e in entries))
            planted = jsonl(stream)
            self.assertNotEqual(planted[-1], planted[-2],
                                f"the growth plant did not land in {stream}")
        return stream

    def usage_tokens(self):
        usage = load_module(self, USAGE, "minspec_usage_reemit")
        rows = list(usage.claude_sessions(self.root / "claude", REPO))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_identical_reemit_is_counted_once_by_usage(self):
        self.reemitted_stream(grown=False)
        row = self.usage_tokens()
        self.assertEqual(row["tokens"], CLAUDE_TOKENS,
                         "usage summed a re-emitted message id")
        self.assertEqual(row["messages"], 2,
                         "usage counted the duplicate as a third message")

    def test_grown_reemit_is_counted_last_wins_by_usage(self):
        self.reemitted_stream(grown=True)
        grown = {
            "input": CLAUDE_TOKENS["input"] + self.GROWTH["input_tokens"],
            "cached": (CLAUDE_TOKENS["cached"]
                       + self.GROWTH["cache_creation_input_tokens"]
                       + self.GROWTH["cache_read_input_tokens"]),
            "output": CLAUDE_TOKENS["output"] + self.GROWTH["output_tokens"],
        }
        grown["total"] = sum(grown.values())
        self.assertEqual(self.usage_tokens()["tokens"], grown,
                         "usage kept the first copy of a grown re-emit")

    def test_colliding_slugs_are_separated_by_entry_cwd(self):
        stores = self.stores()
        actual, colliding = "/a/b-c", "/a-b/c"
        collided_slug = "-a-b-c"
        stores.build_claude_store(
            self.root / "claude", collided_slug, base_timestamp=BASE_EPOCH,
            cwd=actual, session_id=CLAUDE_SESSION, model=CLAUDE_MODEL,
            effort="high", marker=MARKER)
        usage = load_module(self, USAGE, "minspec_usage_collision")
        hit = list(usage.claude_sessions(self.root / "claude", actual))
        miss = list(usage.claude_sessions(self.root / "claude", colliding))
        self.assertEqual([len(hit), len(miss)], [1, 0],
                         "usage --repo trusted the lossy slug over the"
                         " cwd stored in the entries")


class BreakerConsumesTheSharedClaudeFixture(StoreBuilderCase):
    def test_once_trips_above_a_low_cap_and_stays_clean_above_spend(self):
        artifacts = self.build_all(self.root)
        entries = jsonl(artifacts["claude_stream"])
        spend = 0
        for entry in entries:
            usage = entry["message"]["usage"]
            spend += sum(usage.values())

        low_cap = spend - 1
        self.assertGreater(
            spend,
            low_cap,
            (
                f"the over-cap spend was not planted in "
                f"{artifacts['claude_stream']}"
            ),
        )
        self.assertEqual(
            spend,
            CLAUDE_TOKENS["total"],
            (
                f"{artifacts['claude_stream']} contains an unexpected "
                "planted spend"
            ),
        )

        tripped = subprocess.run(
            [
                sys.executable,
                str(BREAKER),
                str(artifacts["claude_stream"]),
                "--once",
                "--cap",
                str(low_cap),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            tripped.returncode,
            3,
            (
                f"{BREAKER} did not trip on the planted Claude spend: "
                f"{tripped.stderr}"
            ),
        )

        clean = subprocess.run(
            [
                sys.executable,
                str(BREAKER),
                str(artifacts["claude_stream"]),
                "--once",
                "--cap",
                str(spend + 1),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            clean.returncode,
            0,
            (
                f"{BREAKER} tripped even though the Claude spend was "
                f"below cap: {clean.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
