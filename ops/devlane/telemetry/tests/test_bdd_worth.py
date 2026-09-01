"""BDD traceability cases joining worth behaviors covered separately elsewhere."""

import json
import unittest

import test_worth as worth_contract


class WorthScenarios(unittest.TestCase):
    def setUp(self):
        self.fixture = worth_contract.WorthContractTests(
            "test_default_window_boundaries_and_independent_edge_overrides"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_report_joins_window_cost_results_and_stamp(self):
        """Scenario: the report joins spend and results for a window"""
        w = self.fixture
        base = worth_contract.iso_epoch("2026-08-22T10:00:00.000Z")
        w._plant_standard_claude(
            "joined-claude",
            "2026-08-22T10:00:00.000Z",
        )
        w._plant_standard_codex(
            "joined-codex",
            "2026-08-22T10:00:00.000Z",
        )
        w._plant_grok(
            "joined-grok",
            "2026-08-22T10:00:00.000Z",
            [[{
                "inputTokens": 70,
                "cachedReadTokens": 10,
                "cacheCreationTokens": 5,
                "outputTokens": 15,
                "totalTokens": 100,
                "reasoningTokens": 2,
                "costUsdTicks": 9,
            }]],
        )
        self.assertGreater(base, 0)

        w._git("switch", "-q", "-c", "pr-7")
        w._commit_files(
            {"src/joined.txt": "joined report fixture\n"},
            "implement joined report fixture",
            "2026-08-22T10:30:00.000Z",
        )
        w._git("switch", "-q", "dev")
        merge = w._merge(
            "pr-7",
            "Merge pull request #7 from fixtures/pr-7",
            "2026-08-22T10:40:00.000Z",
        )
        parents = w._git("show", "-s", "--format=%P", merge).stdout.split()
        self.assertEqual(len(parents), 2, "the numbered-merge plant did not land")

        since = "2026-08-22T09:00:00.000Z"
        until = "2026-08-22T12:00:00.000Z"
        now = "2026-08-22T13:00:00.000Z"
        args = ("--since", since, "--until", until)

        plain = w._run_worth("report", *args, now=now)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        data = w._json_worth("report", *args, now=now)

        expected_totals = {
            "claude": 12_530,
            "codex": 490,
            "grok": 100,
        }
        for harness, total in expected_totals.items():
            record = w._harness_record(data, harness)
            w._assert_key_number(record, ("total",), total)
            line = w._line_for_harness(plain.stdout, harness)
            w._assert_plain_number(line, ("total",), total)

        self.assertEqual(w._pr_numbers(data["results"]), {7})
        w._assert_stamp(plain.stdout, data, since, until, now)

    def test_waste_combines_ranking_ties_and_heavy_turn_identity(self):
        """Scenario: waste ranks the window's sessions and names the heavy turn"""
        w = self.fixture
        w._plant_claude(
            "leader",
            [
                {
                    "id": "leader-small",
                    "timestamp": "2026-08-22T10:00:00.000Z",
                    "output": 10,
                },
                {
                    "id": "leader-heavy",
                    "timestamp": "2026-08-22T10:01:00.000Z",
                    "output": 300,
                },
            ],
        )
        w._plant_claude(
            "tie-a",
            [{
                "id": "tie-a-message",
                "timestamp": "2026-08-22T10:02:00.000Z",
                "output": 200,
            }],
        )
        w._plant_grok(
            "tie-b",
            "2026-08-22T10:00:00.000Z",
            [[{
                "inputTokens": 140,
                "cachedReadTokens": 10,
                "cacheCreationTokens": 0,
                "outputTokens": 50,
                "totalTokens": 200,
                "reasoningTokens": 0,
                "costUsdTicks": 1,
            }]],
        )

        data = w._json_worth(
            "waste",
            "--since",
            "2026-08-22T09:00:00.000Z",
            "--until",
            "2026-08-22T12:00:00.000Z",
            "--top",
            "3",
            now="2026-08-22T13:00:00.000Z",
        )

        self.assertEqual(
            w._session_ids(data),
            ["leader", "tie-a", "tie-b"],
            "sessions did not rank by window total and then id",
        )
        heavy = [
            signal
            for signal in w._signals_of_kind(data, "heavy-turn")
            if (
                w._signal_field(signal, ("harness",)) == "claude"
                and w._signal_field(
                    signal,
                    ("session", "session_id", "id"),
                ) == "leader"
            )
        ]
        self.assertEqual(len(heavy), 1, data["signals"])
        rendered = json.dumps(heavy[0], sort_keys=True)
        self.assertIn("leader-heavy", rendered)
        self.assertIn("300", rendered)


if __name__ == "__main__":
    unittest.main()
