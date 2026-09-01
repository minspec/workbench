"""Regression contract for the Codex fixture shape from PR #24."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
STORES = HERE.parents[1] / "stores.py"
BASE_EPOCH = 1_787_306_400
REPO = "/home/work/projects/minspec/workbench"
SESSION = "77777777-7777-4777-8777-777777777777"
MODEL = "gpt-5-codex"


def load_module(testcase, path, name):
    testcase.assertTrue(path.is_file(), f"required module is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    testcase.assertIsNotNone(spec, f"could not create an import spec for {path}")
    testcase.assertIsNotNone(spec.loader, f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoreFindings(unittest.TestCase):
    def test_b7_codex_fixture_splits_session_meta_from_later_turn_context(self):
        with tempfile.TemporaryDirectory(prefix="stores-findings-") as temp:
            root = Path(temp) / "codex"
            stores = load_module(self, STORES, "minspec_stores_findings")
            stores.build_codex_store(
                root,
                base_timestamp=BASE_EPOCH,
                cwd=REPO,
                session_id=SESSION,
                model=MODEL,
                effort="high",
                marker="FIXTUREPROMPTMARKER",
            )
            rollouts = list(root.glob("sessions/*/*/*/rollout-*.jsonl"))
            self.assertEqual(
                len(rollouts),
                1,
                f"the Codex builder emitted {len(rollouts)} rollout files",
            )
            entries = [
                json.loads(line)
                for line in rollouts[0].read_text().splitlines()
                if line.strip()
            ]

        meta_indexes = [
            index
            for index, entry in enumerate(entries)
            if entry.get("type") == "session_meta"
        ]
        context_indexes = [
            index
            for index, entry in enumerate(entries)
            if entry.get("type") == "turn_context"
        ]
        self.assertEqual(
            meta_indexes,
            [0],
            "the fixture must begin with one session_meta",
        )
        self.assertEqual(
            len(context_indexes),
            1,
            "the fixture must contain one separate turn_context",
        )
        self.assertGreater(
            context_indexes[0],
            meta_indexes[0],
            "turn_context must occur after session_meta",
        )

        meta = entries[meta_indexes[0]]["payload"]
        context = entries[context_indexes[0]]["payload"]
        self.assertLess(
            entries[meta_indexes[0]]["timestamp"],
            entries[context_indexes[0]]["timestamp"],
            "turn_context must carry a timestamp later than session_meta",
        )
        self.assertEqual(
            (meta.get("id"), meta.get("cwd")),
            (SESSION, REPO),
            "session_meta must carry the fixture session id and cwd",
        )
        self.assertEqual(
            (context.get("cwd"), context.get("model")),
            (REPO, MODEL),
            "turn_context must carry the fixture cwd and model",
        )
        self.assertNotIn(
            "id",
            context,
            "turn_context must not synthesize a duplicate session id",
        )
        self.assertNotIn(
            "model",
            meta,
            "session_meta must not synthesize the later model fact",
        )


if __name__ == "__main__":
    unittest.main()
