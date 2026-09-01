"""Contracts for Grok usage emission by the shared store fixture."""

import hashlib
import importlib.util
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve()
STORES = HERE.parents[1] / "stores.py"

BASE_EPOCH = 1_787_306_400
SECOND_BASE_EPOCH = BASE_EPOCH + 86_400
REPO = "/home/work/projects/minspec/workbench"
SESSION = "grok-usage-fixture"
MODEL = "grok-4.6"
MARKER = "GROKUSAGEFIXTUREMARKER"

LEGACY_STORE_SHA256 = (
    "49ab593d38217dcf1ee0f7d2443da422b61e77aa08f8e387b2208ee10f0a74e0"
)

USAGE_RUNS = [
    [
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
    ],
    [
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
        }
    ],
]


def load_module(testcase):
    testcase.assertTrue(
        STORES.is_file(),
        f"{STORES} is missing; the fixture contract requires it",
    )
    spec = importlib.util.spec_from_file_location(
        "minspec_grok_usage_stores",
        STORES,
    )
    testcase.assertIsNotNone(spec)
    testcase.assertIsNotNone(spec.loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jsonl(path):
    return [
        json.loads(raw)
        for raw in path.read_text().splitlines()
        if raw.strip()
    ]


def session_path(root, session=SESSION):
    return (
        Path(root)
        / "sessions"
        / quote(REPO, safe="")
        / session
    )


def usage_entries(entries):
    found = []
    for entry in entries:
        update = (
            (entry.get("params") or {}).get("update") or {}
        )
        if update.get("sessionUpdate") == "turn_completed":
            found.append(entry)
    return found


def legacy_digest(session):
    files = sorted(
        path
        for path in session.rglob("*")
        if path.is_file()
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(session).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class GrokUsageFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="grok-usage-fixture-"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stores = load_module(self)

    def assert_usage_keyword(self):
        parameter = inspect.signature(
            self.stores.build_grok_store
        ).parameters.get("usage_runs")
        self.assertIsNotNone(
            parameter,
            (
                "build_grok_store must expose an explicit usage_runs "
                "keyword"
            ),
        )
        self.assertIsNot(
            parameter.default,
            inspect.Parameter.empty,
            (
                "usage_runs must be optional so legacy fixture calls "
                "remain valid"
            ),
        )

    def build_usage_store(self, root, base_timestamp):
        self.stores.build_grok_store(
            root,
            REPO,
            base_timestamp=base_timestamp,
            session_id=SESSION,
            model=MODEL,
            marker=MARKER,
            usage_runs=json.loads(json.dumps(USAGE_RUNS)),
        )
        return session_path(root)

    def test_usage_runs_round_trip_in_measured_shape_and_deterministic_time(self):
        self.assert_usage_keyword()

        first = self.build_usage_store(
            self.root / "first",
            BASE_EPOCH,
        )
        repeated = self.build_usage_store(
            self.root / "repeated",
            BASE_EPOCH,
        )
        shifted = self.build_usage_store(
            self.root / "shifted",
            SECOND_BASE_EPOCH,
        )

        first_updates = jsonl(first / "updates.jsonl")
        repeated_updates = jsonl(repeated / "updates.jsonl")
        shifted_updates = jsonl(shifted / "updates.jsonl")

        self.assertEqual(
            (first / "updates.jsonl").read_bytes(),
            (repeated / "updates.jsonl").read_bytes(),
            (
                "identical usage fixture arguments did not produce "
                "byte-identical updates.jsonl"
            ),
        )

        legacy_updates = [
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "kind": "tool",
                        "detail": MARKER,
                    }
                },
                "timestamp": BASE_EPOCH + 20,
            },
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "kind": "note",
                        "detail": MARKER,
                    }
                },
                "timestamp": BASE_EPOCH + 60,
            },
        ]
        self.assertEqual(
            first_updates[:2],
            legacy_updates,
            "usage emission changed the existing Grok update records",
        )

        emitted = usage_entries(first_updates)
        repeated_emitted = usage_entries(repeated_updates)
        shifted_emitted = usage_entries(shifted_updates)
        expected_usage = [
            usage
            for run in USAGE_RUNS
            for usage in run
        ]

        self.assertEqual(
            len(emitted),
            len(expected_usage),
            "not every cumulative usage event was emitted",
        )
        self.assertEqual(
            [entry["params"]["update"]["usage"] for entry in emitted],
            expected_usage,
            "the pinned usage values did not round-trip",
        )
        self.assertEqual(
            [entry["params"]["update"]["usage"] for entry in repeated_emitted],
            expected_usage,
        )
        self.assertEqual(
            [entry["params"]["update"]["usage"] for entry in shifted_emitted],
            expected_usage,
        )

        for index, entry in enumerate(emitted):
            with self.subTest(event=index):
                self.assertEqual(
                    set(entry),
                    {"method", "params", "timestamp"},
                )
                self.assertEqual(entry["method"], "session/update")
                self.assertEqual(set(entry["params"]), {"update"})
                self.assertEqual(
                    set(entry["params"]["update"]),
                    {"sessionUpdate", "usage"},
                )
                self.assertEqual(
                    entry["params"]["update"]["sessionUpdate"],
                    "turn_completed",
                )
                self.assertIs(type(entry["timestamp"]), int)

        first_stamps = [entry["timestamp"] for entry in emitted]
        shifted_stamps = [entry["timestamp"] for entry in shifted_emitted]
        self.assertEqual(
            first_stamps,
            sorted(first_stamps),
            "usage timestamps must preserve stream order",
        )
        self.assertEqual(
            len(set(first_stamps)),
            len(first_stamps),
            "usage events need distinct deterministic timestamps",
        )
        self.assertEqual(
            shifted_stamps,
            [
                timestamp + SECOND_BASE_EPOCH - BASE_EPOCH
                for timestamp in first_stamps
            ],
            "usage timestamps are not derived from base_timestamp",
        )

        raw_store = "\n".join(
            path.read_text()
            for path in sorted(first.iterdir())
            if path.is_file()
        )
        self.assertIn(
            MARKER,
            raw_store,
            "the content leak marker was not planted in the Grok store",
        )
        self.assertNotIn(
            MARKER,
            json.dumps(expected_usage, sort_keys=True),
            "the content marker leaked into a usage field",
        )
        summary = json.loads((first / "summary.json").read_text())
        self.assertEqual(summary["info"]["id"], SESSION)
        self.assertEqual(summary["info"]["cwd"], REPO)

    def test_omitting_usage_runs_preserves_the_legacy_store_bytes(self):
        self.assert_usage_keyword()

        root = self.root / "legacy"
        self.stores.build_grok_store(
            root,
            REPO,
            base_timestamp=BASE_EPOCH,
            session_id="grok-fixture",
            model=MODEL,
            marker="FIXTUREPROMPTMARKER",
        )
        session = session_path(root, session="grok-fixture")
        paths = {
            path.relative_to(session).as_posix()
            for path in session.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            paths,
            {"events.jsonl", "summary.json", "updates.jsonl"},
            "the default Grok fixture changed its artifact set",
        )
        self.assertEqual(
            legacy_digest(session),
            LEGACY_STORE_SHA256,
            (
                "omitting usage_runs must build the pre-usage Grok "
                "store byte-identically"
            ),
        )
        self.assertEqual(
            usage_entries(jsonl(session / "updates.jsonl")),
            [],
            "the default fixture invented usage events",
        )


if __name__ == "__main__":
    unittest.main()
