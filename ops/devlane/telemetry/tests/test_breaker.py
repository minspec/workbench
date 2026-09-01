"""The tripwire battery, against fixture streams in the real formats.

Adapted from loopstrap's token-breaker.py (the live supervision layer;
usage.py is the after-the-fact layer). Every wire test proves both
directions: the wire fires on the planted condition AND stays quiet on the
clean stream — a wire that only fires proves nothing about its silence.
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BREAKER = Path(__file__).resolve().parents[1] / "breaker.py"
EXIT_TRIPPED = 3


def claude_line(mid, out=10, inp=5, cached=100, content=None):
    return json.dumps({"type": "assistant", "message": {
        "id": mid, "model": "claude-fable-5",
        "usage": {"input_tokens": inp, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": cached, "output_tokens": out},
        "content": content or []}}) + "\n"


def tool_use(name, arg):
    return {"type": "tool_use", "name": name, "input": {"cmd": arg}}


def tool_result(error=False, text="ok"):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": error, "content": text}]}}) + "\n"


def codex_line(total, out):
    return json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {
            "input_tokens": total - out, "cached_input_tokens": 0,
            "output_tokens": out, "total_tokens": total}}}}) + "\n"


class BreakerCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="breaker-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.stream = self.dir / "stream.jsonl"

    def run_once(self, *args):
        return subprocess.run(
            [sys.executable, str(BREAKER), str(self.stream), "--once", *args],
            capture_output=True, text=True, check=False)


class TokenWalls(BreakerCase):
    def test_the_total_wall_trips_and_names_its_numbers(self):
        self.stream.write_text(claude_line("m1", out=50, cached=400)
                               + claude_line("m2", out=50, cached=400))
        proc = self.run_once("--cap", "500")
        self.assertEqual(proc.returncode, EXIT_TRIPPED, proc.stderr)
        self.assertIn("tokens", proc.stderr)
        self.assertIn("500", proc.stderr, "the cap belongs in the evidence")

    def test_the_wall_stays_quiet_under_the_cap(self):
        self.stream.write_text(claude_line("m1"))
        self.assertEqual(self.run_once("--cap", "500").returncode, 0)

    def test_the_output_wall_is_its_own_currency(self):
        self.stream.write_text(claude_line("m1", out=600, cached=0))
        proc = self.run_once("--cap-out", "500")
        self.assertEqual(proc.returncode, EXIT_TRIPPED)
        self.assertIn("tokens-out", proc.stderr)

    def test_a_rewritten_message_id_is_counted_once(self):
        # Snapshot overwrites re-emit the same message id; summing both
        # copies would double the spend (loopstrap's accounting guard).
        self.stream.write_text(claude_line("m1", out=300, cached=0)
                               + claude_line("m1", out=300, cached=0))
        self.assertEqual(self.run_once("--cap", "400").returncode, 0,
                         "the same message id was double-counted")

    def test_codex_cumulative_counts_are_not_summed(self):
        self.stream.write_text(codex_line(300, 30) + codex_line(450, 60))
        self.assertEqual(self.run_once("--cap", "500").returncode, 0,
                         "cumulative token_count events were summed")
        proc = self.run_once("--cap", "440")
        self.assertEqual(proc.returncode, EXIT_TRIPPED)


class Storms(BreakerCase):
    def test_a_repeat_loop_trips(self):
        lines = "".join(
            json.dumps({"type": "assistant", "message": {
                "id": f"m{i}", "usage": {"output_tokens": 1},
                "content": [tool_use("Bash", "same-cmd")]}}) + "\n"
            for i in range(6))
        self.stream.write_text(lines)
        proc = self.run_once("--repeat-n", "5", "--repeat-k", "8")
        self.assertEqual(proc.returncode, EXIT_TRIPPED, proc.stderr)
        self.assertIn("repeat-loop", proc.stderr)

    def test_varied_calls_stay_quiet(self):
        lines = "".join(
            json.dumps({"type": "assistant", "message": {
                "id": f"m{i}", "usage": {"output_tokens": 1},
                "content": [tool_use("Bash", f"cmd-{i}")]}}) + "\n"
            for i in range(8))
        self.stream.write_text(lines)
        self.assertEqual(
            self.run_once("--repeat-n", "5", "--repeat-k", "8").returncode, 0)

    def test_an_error_storm_trips_and_a_healthy_mix_does_not(self):
        noisy = "".join(tool_result(error=True, text="boom") for _ in range(12))
        self.stream.write_text(noisy)
        proc = self.run_once("--err-min", "10", "--window", "12")
        self.assertEqual(proc.returncode, EXIT_TRIPPED)
        self.assertIn("error-storm", proc.stderr)
        self.stream.write_text(tool_result(error=True) + tool_result() * 20)
        self.assertEqual(
            self.run_once("--err-min", "10", "--window", "12").returncode, 0)


class SizeWireIsVendorAgnostic(BreakerCase):
    def test_any_format_trips_on_size(self):
        # Grok records no tokens; bytes are the wire that still works.
        self.stream.write_text('{"noise": "' + "x" * 2_000_000 + '"}\n')
        proc = self.run_once("--size-mb", "1")
        self.assertEqual(proc.returncode, EXIT_TRIPPED)
        self.assertIn("size", proc.stderr)


class LiveSupervision(BreakerCase):
    def spawn(self, *args):
        return subprocess.Popen(
            [sys.executable, str(BREAKER), str(self.stream), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_stall_trips_while_the_watched_process_lives(self):
        self.stream.write_text(claude_line("m1"))
        sleeper = subprocess.Popen([sys.executable, "-c",
                                    "import time; time.sleep(60)"])
        self.addCleanup(sleeper.kill)
        proc = self.spawn("--pid", str(sleeper.pid),
                          "--stall", "1", "--interval", "0.2")
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("the breaker never tripped on a stalled stream")
        self.assertEqual(proc.returncode, EXIT_TRIPPED, err)
        self.assertIn("stall", err)

    def test_a_dead_process_drains_and_exits_clean(self):
        """Scenario: a dead reviewer ends supervision without a trip"""
        self.stream.write_text(claude_line("m1"))
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        proc = self.spawn("--pid", str(dead.pid),
                          "--stall", "30", "--interval", "0.2")
        _, err = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, err)

    def test_terminate_takes_the_process_down_on_a_trip(self):
        self.stream.write_text(claude_line("m1", out=900, cached=0))
        runaway = subprocess.Popen([sys.executable, "-c",
                                    "import time; time.sleep(60)"])
        self.addCleanup(runaway.kill)
        proc = self.spawn("--pid", str(runaway.pid), "--terminate",
                          "--cap", "100", "--interval", "0.2")
        _, err = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, EXIT_TRIPPED, err)
        deadline = time.time() + 10
        while time.time() < deadline and runaway.poll() is None:
            time.sleep(0.2)
        self.assertIsNotNone(runaway.poll(),
                             "--terminate must actually stop the runaway")


class TheTripLeavesEvidence(BreakerCase):
    def test_the_tripped_file_carries_wire_and_numbers(self):
        """Scenario: the token wall trips a reviewer that spent past its cap"""
        self.stream.write_text(claude_line("m1", out=900, cached=0))
        flag = self.dir / "TRIPPED.md"
        proc = self.run_once("--cap", "100", "--tripped-file", str(flag))
        self.assertEqual(proc.returncode, EXIT_TRIPPED)
        text = flag.read_text()
        # the heading names the wall that FIRED; the evidence block
        # below always mentions "tokens", so the discriminator is
        # the heading (Grok, PR #33 review — an unnamed-trip mutant
        # survived the substring)
        # newline-terminated: the "tokens" prefix must not accept a
        # "tokens-out" heading (Grok, PR #33 delta round 2)
        self.assertIn("# TRIPPED \u2014 tokens\n", text)
        self.assertIn("TRIPWIRE tokens:", text)
        self.assertIn("100", text)
        self.assertIn(str(self.stream), text)


class GrownReemitIsCountedLastWins(unittest.TestCase):
    """A snapshot rewrite can re-emit a message id with GROWN usage.
    First-wins keeps the stale copy and under-counts; summing keeps
    both and over-counts; only last-wins reads the stream honestly."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.stream = Path(self.dir.name) / "stream.jsonl"
        original = claude_line("m-grow", out=40, inp=10, cached=50)
        grown = claude_line("m-grow", out=60, inp=20, cached=70)
        self.assertNotEqual(original, grown,
                            "the growth plant did not change the line")
        self.stream.write_text(original + grown)
        self.grown_total = 60 + 20 + 70

    def run_once(self, cap):
        return subprocess.run(
            [sys.executable, str(BREAKER), str(self.stream), "--once",
             "--cap", str(cap)],
            capture_output=True, text=True, check=False)

    def test_trips_just_below_the_grown_spend(self):
        self.assertEqual(self.run_once(self.grown_total - 1).returncode,
                         EXIT_TRIPPED,
                         "first-wins kept the stale copy and missed the trip")

    def test_quiet_at_the_grown_spend(self):
        self.assertEqual(self.run_once(self.grown_total).returncode, 0,
                         "summing both copies of one id tripped a cap the"
                         " real spend never reached")


if __name__ == "__main__":
    unittest.main()
