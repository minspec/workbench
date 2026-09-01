"""Grok usage feeds the breaker's total and output token walls."""

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BREAKER = Path(__file__).resolve().parents[1] / "breaker.py"
EXIT_TRIPPED = 3


def claude_line(mid, out, inp, cached):
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": mid,
                "model": "claude-fable-5",
                "usage": {
                    "input_tokens": inp,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached,
                    "output_tokens": out,
                },
                "content": [],
            },
        }
    ) + "\n"


def codex_line(total, out):
    return json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": total - out,
                        "cached_input_tokens": 0,
                        "output_tokens": out,
                        "total_tokens": total,
                    }
                },
            },
        }
    ) + "\n"


def grok_line(
    usage=None, *, session_update="turn_completed", timestamp=1
):
    update = {"sessionUpdate": session_update}
    if usage is not None:
        update["usage"] = usage
    return json.dumps(
        {
            "method": "session/update",
            "params": {"update": update},
            "timestamp": timestamp,
        }
    ) + "\n"


def grok_reset_lines():
    """Two cumulative runs: 100 + 40 total and 14 + 9 output."""
    return (
        grok_line(
            {
                "inputTokens": 80,
                "outputTokens": 10,
                "totalTokens": 100,
                "reasoningTokens": 900_000,
                "costUsdTicks": 700_000,
            },
            timestamp=1,
        ),
        grok_line(
            {
                "outputTokens": 14,
                "reasoningTokens": 1_000_000,
                "costUsdTicks": 800_000,
            },
            timestamp=2,
        ),
        grok_line(
            {"inputTokens": 86, "totalTokens": 100},
            timestamp=3,
        ),
        grok_line(
            {
                "inputTokens": 30,
                "outputTokens": 5,
                "totalTokens": 40,
                "reasoningTokens": 2_000_000,
                "costUsdTicks": 900_000,
            },
            timestamp=4,
        ),
        grok_line(
            {"outputTokens": 9, "costUsdTicks": 950_000},
            timestamp=5,
        ),
        grok_line(
            {"inputTokens": 31, "totalTokens": 40},
            timestamp=6,
        ),
    )


class BreakerGrokCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="breaker-grok-")
        self.addCleanup(self.tempdir.cleanup)
        self.stream = Path(self.tempdir.name) / "updates.jsonl"

    def write_stream(self, lines):
        planted = "".join(lines)
        self.stream.write_text(planted)
        self.assertEqual(
            self.stream.read_text(),
            planted,
            "the Grok stream plant did not land byte-for-byte",
        )

    def run_once(self, *args):
        return subprocess.run(
            [sys.executable, str(BREAKER), str(self.stream), "--once", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def return_codes(self, lines, cases):
        self.write_stream(lines)
        return tuple(self.run_once(*args).returncode for args in cases)


class GrokRecordSelection(BreakerGrokCase):
    def test_only_completed_turns_with_usage_dicts_are_accounted(self):
        lines = (
            grok_line(
                {"totalTokens": 5_000, "outputTokens": 4_000},
                session_update="tool_completed",
                timestamp=1,
            ),
            grok_line(session_update="turn_cancelled", timestamp=2),
            grok_line([{"totalTokens": 6_000}], timestamp=3),
            grok_line(
                {"totalTokens": 120, "outputTokens": 30},
                timestamp=4,
            ),
        )
        observed = self.return_codes(
            lines,
            (
                ("--cap", "119"),
                ("--cap", "120"),
                ("--cap-out", "29"),
                ("--cap-out", "30"),
            ),
        )
        self.assertEqual(observed, (EXIT_TRIPPED, 0, EXIT_TRIPPED, 0))


class GrokMalformedRecords(BreakerGrokCase):
    def test_list_params_neither_crash_nor_count(self):
        lines = (
            json.dumps({"method": "x", "params": [1, 2],
                        "timestamp": 1}) + "\n",
            grok_line({"totalTokens": 120, "outputTokens": 30},
                      timestamp=2),
        )
        self.assertIn('"params": [1, 2]', lines[0])
        observed = self.return_codes(
            lines, (("--cap", "119"), ("--cap", "120")))
        self.assertEqual(observed, (EXIT_TRIPPED, 0))

    def test_non_object_json_lines_neither_crash_nor_count(self):
        # the batch CARRIES real usage: a feed() that unwraps or
        # recursively feeds list items would count the 5000 and trip
        # the 120 wall — ignoring the whole non-dict line does not
        batch = [{"params": {"update": {
            "sessionUpdate": "turn_completed",
            "usage": {"totalTokens": 5_000, "outputTokens": 4_000}}}}]
        lines = (
            json.dumps(batch) + "\n",
            json.dumps(42) + "\n",
            json.dumps(None) + "\n",
            grok_line({"totalTokens": 120, "outputTokens": 30},
                      timestamp=2),
        )
        self.assertTrue(lines[0].startswith("["))
        self.assertIn('"totalTokens": 5000', lines[0])
        self.assertEqual(lines[1].strip(), "42")
        observed = self.return_codes(
            lines, (("--cap", "119"), ("--cap", "120")))
        self.assertEqual(observed, (EXIT_TRIPPED, 0))

    def test_skip_is_distinguished_from_coerce_and_zero(self):
        # last-report-wins can mask a coercion: "9999" then 120 banks
        # a phantom split if the string is coerced (9999 -> shrink),
        # and treating null as 0 ERASES a prior numeric out. Both
        # wrong accountings move a wall; skip does not.
        lines = (
            grok_line({"totalTokens": "9999", "outputTokens": 5},
                      timestamp=1),
            grok_line({"totalTokens": 120, "outputTokens": 30},
                      timestamp=2),
            grok_line({"totalTokens": 130, "outputTokens": None},
                      timestamp=3),
        )
        self.assertIn('"totalTokens": "9999"', lines[0])
        self.assertIn('"outputTokens": null', lines[2])
        observed = self.return_codes(
            lines,
            (("--cap", "129"), ("--cap", "130"),
             ("--cap-out", "29"), ("--cap-out", "30")),
        )
        # skip: one run, total 130, out 30. coerce would bank 9999+
        # (tripping 130); zeroing null would erase out 30 (quiet 29).
        self.assertEqual(observed, (EXIT_TRIPPED, 0, EXIT_TRIPPED, 0))

    def test_non_numeric_counters_neither_crash_nor_count(self):
        lines = (
            grok_line({"totalTokens": "100", "outputTokens": 5},
                      timestamp=1),
            grok_line({"totalTokens": 100, "outputTokens": None},
                      timestamp=2),
            grok_line({"totalTokens": 120, "outputTokens": 30},
                      timestamp=3),
        )
        self.assertIn('"totalTokens": "100"', lines[0])
        self.assertIn('"outputTokens": null', lines[1])
        # the string total is not a report, so no reset splits; the
        # null output never enters the sum; the walls answer for the
        # numeric report alone
        observed = self.return_codes(
            lines,
            (("--cap", "119"), ("--cap", "120"),
             ("--cap-out", "29"), ("--cap-out", "30")),
        )
        self.assertEqual(observed, (EXIT_TRIPPED, 0, EXIT_TRIPPED, 0))

    """Adversarial-run survivors, pinned: malformed shapes must count
    nothing and crash nothing."""

    def test_string_usage_and_null_params_neither_crash_nor_count(self):
        lines = (
            json.dumps({"method": "session/update", "params": None,
                        "timestamp": 1}) + "\n",
            grok_line('{"totalTokens": 9000}', timestamp=2),
            grok_line({"totalTokens": 120, "outputTokens": 30},
                      timestamp=3),
        )
        self.assertIn('"params": null', lines[0],
                      "the null-params plant did not land")
        self.assertIn('"usage": "{', lines[1],
                      "the string-usage plant did not land")
        observed = self.return_codes(
            lines, (("--cap", "119"), ("--cap", "120")))
        self.assertEqual(observed, (EXIT_TRIPPED, 0),
                         "malformed records crashed or counted")

    def test_after_a_reset_unreported_currencies_start_from_nothing(self):
        lines = (
            grok_line({"totalTokens": 100, "outputTokens": 14},
                      timestamp=1),
            grok_line({"totalTokens": 40}, timestamp=2),
        )
        observed = self.return_codes(
            lines, (("--cap-out", "13"), ("--cap-out", "14")))
        self.assertEqual(
            observed, (EXIT_TRIPPED, 0),
            "the old run's outputTokens leaked into the new run —"
            " current state was not cleared at the bank")


class GrokCumulativeRuns(BreakerGrokCase):
    def test_total_wall_banks_finished_and_current_runs(self):
        """Scenario: grok usage feeds the walls across a run reset"""
        observed = self.return_codes(
            grok_reset_lines(),
            (("--cap", "139"), ("--cap", "140")),
        )
        self.assertEqual(observed, (EXIT_TRIPPED, 0))

    def test_output_wall_banks_last_report_per_run(self):
        observed = self.return_codes(
            grok_reset_lines(),
            (("--cap-out", "22"), ("--cap-out", "23")),
        )
        self.assertEqual(observed, (EXIT_TRIPPED, 0))

    def test_trip_evidence_includes_grok_total_and_output(self):
        self.write_stream(grok_reset_lines())
        proc = self.run_once("--cap", "139")
        expected_totals = "tokens : 140 total / 23 output"
        self.assertEqual(
            (proc.returncode, expected_totals in proc.stderr),
            (EXIT_TRIPPED, True),
            proc.stderr,
        )


class ExistingAccountingStaysPinned(BreakerGrokCase):
    def test_non_grok_totals_stay_pinned_and_mixed_adds_each_once(self):
        claude = (
            claude_line("m1", out=5, inp=5, cached=5),
            claude_line("m1", out=13, inp=17, cached=20),
        )
        codex = (codex_line(40, 11), codex_line(70, 19))
        grok = (
            grok_line({"totalTokens": 15, "outputTokens": 3}, timestamp=1),
            grok_line({"totalTokens": 30, "outputTokens": 7}, timestamp=2),
        )
        currency_cases = (
            ("--cap", "49"),
            ("--cap", "50"),
            ("--cap-out", "12"),
            ("--cap-out", "13"),
        )
        claude_observed = self.return_codes(claude, currency_cases)
        codex_observed = self.return_codes(
            codex,
            (
                ("--cap", "69"),
                ("--cap", "70"),
                ("--cap-out", "18"),
                ("--cap-out", "19"),
            ),
        )
        mixed_observed = self.return_codes(
            claude + codex + grok,
            (
                ("--cap", "149"),
                ("--cap", "150"),
                ("--cap-out", "38"),
                ("--cap-out", "39"),
            ),
        )
        self.assertEqual(
            (claude_observed, codex_observed, mixed_observed),
            (
                (EXIT_TRIPPED, 0, EXIT_TRIPPED, 0),
                (EXIT_TRIPPED, 0, EXIT_TRIPPED, 0),
                (EXIT_TRIPPED, 0, EXIT_TRIPPED, 0),
            ),
        )


class GrokWireDocumentation(unittest.TestCase):
    def test_token_wire_rows_name_grok(self):
        docstring = ast.get_docstring(ast.parse(BREAKER.read_text()))
        self.assertIsNotNone(docstring, "breaker.py lost its module docstring")
        wire_rows = {}
        for line in docstring.splitlines():
            fields = line.split()
            if fields and fields[0] in {"tokens", "tokens-out"}:
                wire_rows[fields[0]] = line
        self.assertEqual(set(wire_rows), {"tokens", "tokens-out"})
        missing = [
            wire for wire, row in wire_rows.items() if "grok" not in row.lower()
        ]
        self.assertEqual(
            missing,
            [],
            f"Grok is absent from these token wire rows: {missing}",
        )


if __name__ == "__main__":
    unittest.main()
