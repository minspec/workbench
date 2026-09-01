"""U10 fix cycle — tests for skeptic 2fd473 over grok's tests.

Authored from in/BRIEF-harness-u10-fix-tests-3.md, D-ENV-2/3 as
ratified, .dev/design/features/dispatch/structured-envelopes.feature,
and the skeptic report. launch.py was not read.

2fd473 findings, each a test here or a stated call in this docstring:

P1 grok source-order rewritten to fit the code — GrokLastValidObject
   restores the discriminating plants (earlier valid vs trailing
   nested-invalid; two fully-valid, last wins). Feature says D-ENV-2.
P2 {unresolved} escape on empty/zero usage — WrapperSpendDoesNotInventZeros
   asserts the U5 value; no unresolved disjunction.
P2 merged spend pins one key — WrapperSpendKeepsCached pins input,
   cached, output and total (total arithmetic).
P2 no fenced ```json envelope on grok's plain path — FencedGrokEnvelope
   plus fixtures/envelopes/grok-fenced-json.raw.out.
P3 grok resume drops --model/--prompt-file — pinned on the resume
   scenario in test_launch_envelope_by_construction.py (D-RES-1).
P3 _envelope_cause leaks into the committed record — PrivateEnvelopeCause
   stays out of the on-disk record.
P3 white-box short-circuit replay — converted to E2E TOKEN replay in
   test_launch_envelope_by_construction.py.
P3 test author amended the ratification — REFUTED / discharged at
   9f07bb8 (D-ENV-2/3); this cycle does not edit the ratification.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import launch_support as ls


def _cause_reason(rec):
    cause = (rec.get("result") or {}).get("cause")
    if isinstance(cause, dict):
        return cause.get("reason")
    return cause


def _envelope(rec):
    return (rec.get("result") or {}).get("envelope") or {}


def _spend(rec):
    session = rec.get("session") or {}
    return session.get("spend") or {}


NINE = (
    "job", "status", "verdict", "counts", "findings",
    "artifacts", "spend", "stamp", "note",
)
U5_INPUT, U5_CACHED, U5_OUTPUT = 30, 12000, 500
WRAPPER_INPUT, WRAPPER_OUTPUT = 10, 4
GROK_FENCED_FIXTURE = "grok-fenced-json.raw.out"
ENVELOPES = ls.FIXTURES_DIR / "envelopes"


def _legal(**over):
    env = {
        "job": "plan",
        "status": "ok",
        "verdict": "changes",
        "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
        "findings": [],
        "artifacts": {},
        "spend": {},
        "stamp": {"ref": "x"},
        "note": "legal",
    }
    env.update(over)
    return env


def _line_objects(data):
    out = []
    for ln in data.decode("utf-8").splitlines():
        s = ln.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _has_nine(obj):
    return all(k in obj for k in NINE)


def _nested_counts_ok(obj):
    counts = obj.get("counts")
    if not isinstance(counts, dict):
        return False
    return all(
        type(counts.get(key)) is int
        for key in ("p1", "p2", "p3", "opinions")
    )


class _FixLaunch(ls._TempLaunch):
    def _raw_out(self):
        raw = self.the_job_dir() / "raw.out"
        self.assertTrue(raw.is_file(), "raw.out is missing")
        data = raw.read_bytes()
        self.assertTrue(data, "raw.out is empty")
        return raw, data

    def _dispatch_closed(self, **kwargs):
        code, out, err = self.dispatch(self.argv_for(**kwargs))
        text = self.combined(out, err)
        self.assertNotEqual(
            code, ls.REFUSAL_EXIT,
            f"finished job must not refuse: {text!r}",
        )
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed", rec)
        return rec, text

    def _close_token(self, payload, *, harness="grok"):
        body = payload if isinstance(payload, str) else json.dumps(payload)
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body
        rec, *_ = self._dispatch_closed(
            job="plan", harness=harness, stage="plan",
        )
        _raw, data = self._raw_out()
        return rec, data

    def _assert_schema_invalid(self, rec, *, plant):
        env = _envelope(rec)
        self.assertEqual(
            env.get("status"), "invalid",
            f"nested wrong type must close invalid, not ok: "
            f"plant={plant!r} envelope={env!r} "
            f"cause={ (rec.get('result') or {}).get('cause')!r}",
        )
        self.assertEqual(
            _cause_reason(rec), "schema-invalid",
            f"nested wrong type is schema-invalid, not envelope-parse: "
            f"plant={plant!r} "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={env!r}",
        )


class NestedWrongTypeClosesInvalid(_FixLaunch):
    """_schema_valid must validate nested ENVELOPE_SCHEMA types."""

    def test_counts_empty_object_is_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(counts={}, note="u10-counts-empty")
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(parsed.get("counts"), {}, "plant: counts {} landed")
        self._assert_schema_invalid(rec, plant="counts={}")

    def test_counts_p1_string_is_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        counts = {"p1": "lots", "p2": 0, "p3": 0, "opinions": 0}
        planted = _legal(counts=counts, note="u10-counts-p1-string")
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(
            parsed.get("counts", {}).get("p1"), "lots",
            "plant: counts.p1 string landed",
        )
        self._assert_schema_invalid(rec, plant="counts.p1='lots'")

    def test_findings_string_items_are_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(
            findings=["not a finding"], note="u10-findings-str",
        )
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(
            parsed.get("findings"), ["not a finding"],
            "plant: findings=['not a finding'] landed",
        )
        self._assert_schema_invalid(rec, plant="findings=['not a finding']")

    def test_findings_int_items_are_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(findings=[42], note="u10-findings-int")
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(
            parsed.get("findings"), [42], "plant: findings=[42] landed",
        )
        self._assert_schema_invalid(rec, plant="findings=[42]")

    def test_findings_missing_required_keys_are_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(
            findings=[{"severity": "p1"}],
            note="u10-findings-partial",
        )
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        items = parsed.get("findings")
        self.assertEqual(len(items), 1, "plant: one finding landed")
        self.assertEqual(items[0].get("severity"), "p1")
        self.assertNotIn("where", items[0], "plant: where withheld")
        self.assertNotIn("claim", items[0], "plant: claim withheld")
        self.assertNotIn("reproduce", items[0], "plant: reproduce withheld")
        self._assert_schema_invalid(
            rec, plant="findings=[{severity:p1}] missing required keys",
        )

    def test_artifacts_int_values_are_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(
            artifacts={"plan": 1}, note="u10-artifacts-int",
        )
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(
            parsed.get("artifacts"), {"plan": 1},
            "plant: artifacts int value landed",
        )
        self._assert_schema_invalid(rec, plant="artifacts={'plan': 1}")

    def test_artifacts_object_values_are_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(
            artifacts={"plan": {"inline": "..."}},
            note="u10-artifacts-obj",
        )
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(
            parsed.get("artifacts", {}).get("plan"), {"inline": "..."},
            "plant: artifacts object value landed",
        )
        self._assert_schema_invalid(
            rec, plant="artifacts={'plan': {'inline': '...'}}",
        )

    def test_commit_null_is_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(commit=None, note="u10-commit-null")
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertIn("commit", parsed, "plant: commit key present")
        self.assertIsNone(parsed.get("commit"), "plant: commit is null")
        self._assert_schema_invalid(rec, plant="commit=null")

    def test_stamp_extra_key_is_schema_invalid(self):
        """Scenario: a nested wrong type closes schema-invalid"""
        planted = _legal(
            stamp={"ref": "x", "extra": "no"},
            note="u10-stamp-extra",
        )
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(
            parsed.get("stamp", {}).get("extra"), "no",
            "plant: stamp extra key landed",
        )
        self._assert_schema_invalid(rec, plant="stamp extra key")

    def test_invalid_status_with_approve_verdict_is_not_ok(self):
        """Scenario: status invalid with verdict approve is not an ok envelope"""
        planted = _legal(
            status="invalid", verdict="approve",
            note="u10-invalid-approve",
        )
        rec, data = self._close_token(planted)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(parsed.get("status"), "invalid", "plant: status")
        self.assertEqual(parsed.get("verdict"), "approve", "plant: verdict")
        env = _envelope(rec)
        self.assertNotEqual(
            env.get("status"), "ok",
            "a task that could not look must not close ok with "
            f"verdict=approve: envelope={env!r}",
        )
        self.assertNotEqual(
            env.get("verdict"), "approve",
            "headline rule: invalid must not approve: "
            f"envelope={env!r}",
        )


class GrokLastValidObject(_FixLaunch):
    def _close_two(self, first, second):
        payload = json.dumps(first) + "\n" + json.dumps(second)
        return self._close_token(payload)

    def test_trailing_nested_invalid_loses_to_the_earlier_valid(self):
        """Scenario: a grok trailing nested-invalid object loses to an earlier valid envelope"""
        valid = _legal(note="harness-object")
        decoy = _legal(
            note="trailing-decoy",
            counts={"p1": "scan", "p2": 0, "p3": 0, "opinions": 0},
        )
        rec, data = self._close_two(valid, decoy)
        self.assertIn(b"harness-object", data)
        self.assertIn(b"trailing-decoy", data)
        self.assertGreater(
            data.find(b"trailing-decoy"),
            data.find(b"harness-object"),
            "plant: decoy trails the valid object",
        )
        objects = _line_objects(data)
        self.assertEqual(len(objects), 2, "plant: exactly two objects")
        self.assertTrue(_has_nine(objects[0]), "plant: earlier is nine-key")
        self.assertTrue(
            _nested_counts_ok(objects[0]),
            "plant: earlier object is nested-valid "
            f"(counts={objects[0].get('counts')!r})",
        )
        self.assertTrue(_has_nine(objects[1]), "plant: trailing is nine-key")
        self.assertFalse(
            _nested_counts_ok(objects[1]),
            "plant: trailing fails nested validation "
            f"(counts={objects[1].get('counts')!r})",
        )
        self.assertEqual(objects[-1].get("note"), "trailing-decoy")
        self.assertEqual(objects[-1]["counts"]["p1"], "scan")
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "harness-object",
            "D-ENV-2: trailing nine-key that fails nested validation "
            "must lose to the earlier fully-valid envelope: "
            f"envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_two_fully_valid_objects_the_last_wins(self):
        """Scenario: two fully-valid grok stdout objects - the last wins"""
        draft = _legal(note="draft-envelope")
        final = _legal(note="final-envelope")
        rec, data = self._close_two(draft, final)
        self.assertIn(b"draft-envelope", data)
        self.assertIn(b"final-envelope", data)
        self.assertGreater(
            data.find(b"final-envelope"),
            data.find(b"draft-envelope"),
            "plant: final trails the draft",
        )
        objects = _line_objects(data)
        self.assertEqual(len(objects), 2, "plant: exactly two objects")
        self.assertTrue(_has_nine(objects[0]) and _nested_counts_ok(objects[0]))
        self.assertTrue(_has_nine(objects[1]) and _nested_counts_ok(objects[1]))
        self.assertEqual(objects[0].get("note"), "draft-envelope")
        self.assertEqual(objects[1].get("note"), "final-envelope")
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "final-envelope",
            "D-ENV-2 stated limit: two fully-valid objects, the last "
            f"wins (a first-valid reader ships the draft): envelope={env!r}",
        )
        self.assertNotEqual(
            env.get("note"), "draft-envelope",
            "the earlier fully-valid object must not win: "
            f"envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)


class CodexStdoutFallback(_FixLaunch):
    def test_bare_stdout_object_is_used_when_file_and_agent_message_fail(self):
        """Scenario: a codex stdout last-JSON-object is used when -o and agent_message both fail"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "stdout"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-stdout-bare"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertFalse(
            last.is_file(),
            f"plant: -o file must be absent, found {last}",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"thread.started", data)
        self.assertNotIn(b"agent_message", data)
        self.assertIn(b"u10-codex-stdout-bare", data)
        env = _envelope(rec)
        self.assertNotEqual(
            _cause_reason(rec), "no-last-message",
            "stdout last-JSON-object fallback must still run after "
            "-o and agent_message fail: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={env!r}",
        )
        self.assertEqual(
            env.get("note"), "u10-codex-stdout-bare",
            f"bare stdout object is the envelope: {env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)


class WrapperSpendDoesNotInventZeros(_FixLaunch):
    def test_empty_usage_leaves_the_store_spend(self):
        """Scenario: empty wrapper usage does not overwrite session.spend with zeros"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_WRAPPER_USAGE"] = "empty"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-spend-empty-usage"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8").splitlines()[0])
        self.assertEqual(wrapper.get("usage"), {}, "plant: usage={}")
        self.assertNotIn("total_cost_usd", wrapper, "plant: no cost")
        spend = _spend(rec)
        self.assertIsInstance(spend, dict, spend)
        self.assertNotIn(
            "unresolved", spend,
            "D-ENV-3: usage={} never yields {unresolved} when U5 "
            f"resolved: spend={spend!r}",
        )
        self.assertEqual(
            spend.get("input"), U5_INPUT,
            "U5 store spend (input 30, cached 12000, output 500) "
            f"must survive empty wrapper usage: spend={spend!r}",
        )
        self.assertEqual(spend.get("cached"), U5_CACHED, spend)
        self.assertEqual(spend.get("output"), U5_OUTPUT, spend)
        self.assertEqual(
            spend.get("total"), U5_INPUT + U5_CACHED + U5_OUTPUT,
            f"U5 total arithmetic must survive empty usage: spend={spend!r}",
        )

    def test_all_zero_usage_leaves_the_store_spend(self):
        """Scenario: empty wrapper usage does not overwrite session.spend with zeros"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_WRAPPER_USAGE"] = "zero"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-spend-zero-usage"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8").splitlines()[0])
        self.assertEqual(wrapper.get("usage", {}).get("input_tokens"), 0)
        self.assertEqual(wrapper.get("usage", {}).get("output_tokens"), 0)
        self.assertEqual(wrapper.get("total_cost_usd"), 0.0)
        spend = _spend(rec)
        self.assertIsInstance(spend, dict, spend)
        self.assertNotIn(
            "unresolved", spend,
            "D-ENV-3: all-zero usage never yields {unresolved} when "
            f"U5 resolved: spend={spend!r}",
        )
        self.assertEqual(
            spend.get("input"), U5_INPUT,
            "U5 store spend must survive zero wrapper usage: "
            f"spend={spend!r}",
        )
        self.assertEqual(spend.get("cached"), U5_CACHED, spend)
        self.assertEqual(spend.get("output"), U5_OUTPUT, spend)
        self.assertEqual(
            spend.get("total"), U5_INPUT + U5_CACHED + U5_OUTPUT,
            f"U5 total arithmetic must survive zero usage: spend={spend!r}",
        )


class WrapperSpendKeepsCached(_FixLaunch):
    def test_wrapper_usage_with_cache_tokens_records_cached_and_total(self):
        """Scenario: wrapper usage that names cache tokens keeps cached and total"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_WRAPPER_USAGE"] = "cached"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "0"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-spend-cached"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8").splitlines()[0])
        usage = wrapper.get("usage") or {}
        self.assertEqual(usage.get("input_tokens"), 12, "plant: input")
        self.assertEqual(usage.get("output_tokens"), 3000, "plant: output")
        self.assertEqual(
            usage.get("cache_read_input_tokens"), 480000,
            "plant: cache_read landed",
        )
        self.assertEqual(
            usage.get("cache_creation_input_tokens"), 9000,
            "plant: cache_creation landed",
        )
        spend = _spend(rec)
        self.assertIsInstance(spend, dict, spend)
        self.assertNotIn("unresolved", spend, spend)
        cached = 480000 + 9000
        self.assertEqual(
            spend.get("cached"), cached,
            "cached is cache_creation + cache_read, not dropped: "
            f"spend={spend!r}",
        )
        self.assertEqual(spend.get("input"), 12, spend)
        self.assertEqual(spend.get("output"), 3000, spend)
        self.assertEqual(
            spend.get("total"), 12 + cached + 3000,
            f"total is input+cached+output: spend={spend!r}",
        )

    def test_incomplete_wrapper_usage_does_not_drop_store_cached(self):
        """Scenario: incomplete wrapper usage does not drop the store cached tally"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-spend-keep-store-cached"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8").splitlines()[0])
        usage = wrapper.get("usage") or {}
        self.assertEqual(usage.get("input_tokens"), 10, "plant: wrapper")
        self.assertNotIn(
            "cache_read_input_tokens", usage,
            "plant: wrapper usage has no cache keys",
        )
        spend = _spend(rec)
        self.assertIsInstance(spend, dict, spend)
        self.assertNotIn("unresolved", spend, spend)
        self.assertEqual(
            spend.get("input"), WRAPPER_INPUT,
            "wrapper input 10 is the turn measurement; ignoring the "
            f"wrapper outright is not the merge: spend={spend!r}",
        )
        self.assertEqual(
            spend.get("cached"), U5_CACHED,
            "incomplete wrapper usage (no cache keys) must not drop "
            f"the U5 store cached tally: spend={spend!r}",
        )
        self.assertEqual(
            spend.get("output"), WRAPPER_OUTPUT,
            f"wrapper output 4 is the turn measurement: spend={spend!r}",
        )
        self.assertEqual(
            spend.get("total"),
            WRAPPER_INPUT + U5_CACHED + WRAPPER_OUTPUT,
            "D-ENV-3 total arithmetic is input+cached+output; a total "
            "that omits cached (14) or a missing total is the 489k-"
            f"tokens-vanish shape one level down: spend={spend!r}",
        )


class ArgvPersistedAtLaunch(_FixLaunch):
    def test_launched_record_carries_the_child_argv(self):
        """Scenario: harness.argv is persisted in the launched record"""
        os.environ["TASK_LAUNCH_SLEEP"] = "0.5"
        seen = {}

        def watch():
            deadline = time.time() + 4
            while not self.start_witness.is_file() and time.time() < deadline:
                time.sleep(0.01)
            files = self.record_files()
            seen["n_records"] = len(files)
            if files:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                seen["status"] = data.get("status")
                harness = data.get("harness") or {}
                seen["argv"] = harness.get("argv")
            if self.start_witness.is_file():
                start = json.loads(
                    self.start_witness.read_text(encoding="utf-8"),
                )
                seen["start_argv"] = start.get("argv")

        t = threading.Thread(target=watch)
        t.start()
        self.dispatch(
            self.argv_for(job="plan", harness="grok", stage="plan"),
        )
        t.join(timeout=6)
        self.assertEqual(
            seen.get("status"), "launched",
            f"must sample the launched record: {seen!r}",
        )
        argv = seen.get("argv")
        self.assertIsInstance(argv, list, f"harness.argv type: {seen!r}")
        self.assertTrue(
            argv,
            "harness.argv is persisted at launch, not first at settle: "
            f"seen={seen!r}",
        )
        start = seen.get("start_argv") or []
        self.assertTrue(start, f"child ran: {seen!r}")
        self.assertEqual(
            argv, start,
            "on-disk harness.argv must be the child argv while status "
            f"is launched: record={argv!r} child={start!r}",
        )


class NonZeroExitNamesTheExitInTheNote(_FixLaunch):
    def test_exit_137_keeps_the_harness_note_and_names_the_code(self):
        """Scenario: a non-zero exit keeps the harness note and names the exit"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_NOTE"] = "wrote the plan"
        os.environ["TASK_LAUNCH_EXIT"] = "137"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        env = _envelope(rec)
        note = str(env.get("note") or "")
        self.assertIn(
            "wrote the plan", note,
            "harness note is present, not replaced: "
            f"envelope={env!r}",
        )
        self.assertIn(
            "137", note,
            "exit 137 must appear in the envelope note, not only in "
            f"result.cause: note={note!r} "
            f"cause={ (rec.get('result') or {}).get('cause')!r}",
        )


class FencedGrokEnvelope(_FixLaunch):
    def test_fenced_pretty_printed_json_is_the_envelope(self):
        """Scenario: a grok fenced json envelope on plain stdout is the envelope"""
        path = ENVELOPES / GROK_FENCED_FIXTURE
        self.assertTrue(path.is_file(), f"fixture missing: {path}")
        body = path.read_text(encoding="utf-8")
        self.assertTrue(body.strip(), f"fixture empty: {path}")
        self.assertIn("```json", body, "plant: opening json fence")
        self.assertIn("u10-grok-fenced", body, "plant: envelope note")
        self.assertIn("```", body.rsplit("```json", 1)[-1])
        complete_lines = 0
        for ln in body.splitlines():
            s = ln.strip()
            if not s.startswith("{"):
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and _has_nine(obj):
                complete_lines += 1
        self.assertEqual(
            complete_lines, 0,
            "plant: no single line is a complete nine-key object "
            "(a line-oriented scan would recover it without a fence)",
        )
        interior = body.split("```json", 1)[1]
        interior = interior.split("```", 1)[0]
        planted = json.loads(interior)
        self.assertTrue(_has_nine(planted), "plant: fence holds nine keys")
        self.assertTrue(
            _nested_counts_ok(planted),
            f"plant: fenced object is nested-valid: {planted.get('counts')!r}",
        )
        self.assertEqual(planted.get("note"), "u10-grok-fenced")
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body.rstrip("\n")
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"```json", data, "plant: fence reached raw.out")
        self.assertIn(b"u10-grok-fenced", data)
        env = _envelope(rec)
        blob = str(env.get("note") or "") + str(_cause_reason(rec) or "")
        self.assertNotIn(
            "envelope-parse", blob.lower(),
            "D-ENV-3: a fenced grok envelope is not envelope-parse: "
            f"note={env.get('note')!r} "
            f"cause={ (rec.get('result') or {}).get('cause')!r}",
        )
        self.assertEqual(
            env.get("note"), "u10-grok-fenced",
            "the pretty-printed fenced object is the envelope: "
            f"envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)


class PrivateEnvelopeCause(_FixLaunch):
    def test_private_envelope_cause_key_stays_out_of_the_committed_record(self):
        """Scenario: a committed record does not carry private envelope-scan keys"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_WRAPPER_INVALID"] = "extra"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-private-key"
        os.environ["TASK_LAUNCH_EXIT"] = "1"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        planted = json.loads(data.decode("utf-8"))
        self.assertIn("transcript", planted, "plant: extra key landed")
        self.assertEqual(rec.get("status"), "closed", rec)
        self.assertNotIn(
            "_envelope_cause", rec,
            "private scan key must not land in the committed record: "
            f"keys={sorted(rec)}",
        )
        result = rec.get("result") or {}
        self.assertIsInstance(result, dict, result)
        self.assertNotIn(
            "_envelope_cause", result,
            "private scan key must not land under result: "
            f"result_keys={sorted(result)}",
        )
        body = self.the_record_path().read_text(encoding="utf-8")
        self.assertNotIn(
            '"_envelope_cause"', body,
            "committed record bytes must not carry _envelope_cause: "
            f"body={body[:400]!r}",
        )
