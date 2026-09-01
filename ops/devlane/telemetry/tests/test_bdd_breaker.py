"""BDD traceability cases not already proved by the breaker suites."""

import json
import unittest

import test_breaker as breaker_contract
import test_breaker_grok as grok_contract


class BreakerScenarios(unittest.TestCase):
    def breaker_fixture(self):
        fixture = breaker_contract.TokenWalls(
            "test_the_total_wall_trips_and_names_its_numbers"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def grok_fixture(self):
        fixture = grok_contract.GrokRecordSelection(
            "test_only_completed_turns_with_usage_dicts_are_accounted"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_output_wall_trips_while_total_wall_stays_under_cap(self):
        """Scenario: the output-token wall trips independently of the total wall"""
        fixture = self.breaker_fixture()
        planted = breaker_contract.claude_line(
            "output-heavy", out=600, inp=5, cached=0
        )
        fixture.stream.write_text(planted)
        self.assertEqual(
            fixture.stream.read_text(),
            planted,
            "the output-heavy stream plant did not land",
        )

        proc = fixture.run_once("--cap", "1000", "--cap-out", "500")

        self.assertEqual(
            proc.returncode,
            breaker_contract.EXIT_TRIPPED,
            proc.stderr,
        )
        # the TRIPWIRE line names the wall that fired; the evidence
        # block below it always prints a "tokens :" summary, so the
        # discriminator is the tripwire name, not that string
        self.assertIn("TRIPWIRE tokens-out", proc.stderr)
        self.assertNotIn("TRIPWIRE tokens:", proc.stderr)

    def test_omitted_zero_default_does_not_arm_the_token_wall(self):
        """Scenario: a zero cap is a disarmed wall"""
        fixture = self.breaker_fixture()
        planted = breaker_contract.claude_line(
            "would-trip", out=600, inp=5, cached=0
        )
        fixture.stream.write_text(planted)
        self.assertEqual(
            fixture.stream.read_text(),
            planted,
            "the over-cap stream plant did not land",
        )

        armed = fixture.run_once("--cap", "604")
        disarmed = fixture.run_once()

        self.assertEqual(
            armed.returncode,
            breaker_contract.EXIT_TRIPPED,
            "the control did not prove this stream can trip a nonzero cap",
        )
        self.assertEqual(disarmed.returncode, 0, disarmed.stderr)

    def test_batch_and_non_numeric_records_do_not_charge_or_crash(self):
        """Scenario: a malformed record neither kills the battery nor counts"""
        fixture = self.grok_fixture()
        batch = [{
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "turn_completed",
                    "usage": {
                        "totalTokens": 5000,
                        "outputTokens": 4000,
                    },
                }
            },
            "timestamp": 1,
        }]
        lines = (
            json.dumps(batch) + "\n",
            grok_contract.grok_line(
                {"totalTokens": "9000", "outputTokens": None},
                timestamp=2,
            ),
            grok_contract.grok_line(
                {"totalTokens": 120, "outputTokens": 30},
                timestamp=3,
            ),
        )
        self.assertTrue(lines[0].startswith("["))
        self.assertIn('"totalTokens": 5000', lines[0])
        self.assertIn('"totalTokens": "9000"', lines[1])
        self.assertIn('"outputTokens": null', lines[1])

        observed = fixture.return_codes(
            lines,
            (
                ("--cap", "119"),
                ("--cap", "120"),
                ("--cap-out", "29"),
                ("--cap-out", "30"),
            ),
        )

        self.assertEqual(
            observed,
            (
                grok_contract.EXIT_TRIPPED,
                0,
                grok_contract.EXIT_TRIPPED,
                0,
            ),
            "the walls did not answer solely for the numeric record",
        )


if __name__ == "__main__":
    unittest.main()
