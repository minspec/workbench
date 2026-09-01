"""U10: structured envelopes by construction.

Authored from `.dev/docs/scratch/harness-research.md` §4 / §2 feature
ending / §6 row U10, D-ENV-1 and D-E2E-1/2, and
`.dev/design/features/dispatch/structured-envelopes.feature`.
Implementation (launch.py, jobs.json, envelope.py) was not read.

Skeptic 37802b CHANGES applied: pin the allowed envelope (types,
required, nullable verdict, optional commit) over extra denials; grok
first-source and codex file-vs-agent_message order; mapped BDD
scenarios; captured S2 bytes.

Fake-CLI knobs added in launch_support.py for this unit:
TASK_LAUNCH_WRAPPER=claude|codex|grok|plain, plus
TASK_LAUNCH_WRAPPER_FIELD / _SUBTYPE / _IS_ERROR / _INVALID /
_NARRATION / _RESULT / _DECOY / _DECOY_FIRST / _AGENT_NOTE,
TASK_LAUNCH_NOTE, TASK_LAUNCH_STATUS, TASK_LAUNCH_ENVELOPE_COMMIT.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import launch_support as ls

ENVELOPES = ls.FIXTURES_DIR / "envelopes"
ENVELOPE_PY = ls.APP.parents[0] / "task" / "envelope.py"
NINE = (
    "job", "status", "verdict", "counts", "findings",
    "artifacts", "spend", "stamp", "note",
)
GROK_NARRATION_FIXTURE = "grok-narration-then-object.raw.out"
GROK_S2_FIXTURE = "grok-s2-13f5f2.raw.out"
CLAUDE_MAX_TURNS_FIXTURE = "claude-result-error-max-turns.json"
CODEX_NDJSON_FIXTURE = "codex-ndjson-tail.jsonl"


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


class _U10Launch(ls._TempLaunch):
    def _argv_list(self, witness):
        argv = [str(p) for p in witness["argv"]]
        self.assertTrue(argv, "witness argv is empty")
        return argv

    def _schema_value(self, argv):
        self.assertIn(
            "--json-schema", argv,
            f"--json-schema missing from argv={argv!r}",
        )
        idx = argv.index("--json-schema")
        self.assertLess(
            idx + 1, len(argv),
            "--json-schema is present but has no value",
        )
        value = str(argv[idx + 1])
        self.assertFalse(
            value.startswith("-"),
            f"--json-schema value looks like a flag: {value!r}",
        )
        return value

    def _loaded_schema(self, value):
        if value.lstrip().startswith("{"):
            data = json.loads(value)
        else:
            path = Path(value)
            self.assertTrue(
                path.is_file(),
                f"schema path is missing: {value!r}",
            )
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        return data

    def _exported_schema(self):
        mod = ls.load_path(self, ENVELOPE_PY, "u10_envelope")
        schema = getattr(mod, "ENVELOPE_SCHEMA", None)
        self.assertIsInstance(
            schema, dict,
            "envelope.py must export ENVELOPE_SCHEMA",
        )
        return schema

    def _child_schema_reachable(self, witness, value):
        self.assertTrue(
            witness.get("schema_read"),
            "the child must open --json-schema from its own argv; "
            f"schema_how={witness.get('schema_how')!r} "
            f"schema_path={witness.get('schema_path')!r}",
        )
        if value.lstrip().startswith("{"):
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            self.assertEqual(witness.get("schema_sha"), digest)
            return
        cwd = Path(witness["cwd"]).resolve()
        path = Path(value)
        path = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        job_parent = cwd.parent
        under_cwd = path == cwd or cwd in path.parents
        under_job = path == job_parent or job_parent in path.parents
        self.assertTrue(
            under_cwd or under_job,
            "--json-schema must be a path the sandboxed child can read "
            f"(cwd or job dir), not a tempfile outside: value={value!r} "
            f"cwd={cwd} job={job_parent}",
        )
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(witness.get("schema_sha"), digest)

    def _resume_witness(self, rec):
        self.assertTrue(self.witness.is_file(), "first launch left a witness")
        self.witness.unlink()
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(
            code, 0,
            f"resume must exit 0: {self.combined(out, err)}",
        )
        return self.read_witness()

    def _fixture_bytes(self, name):
        path = ENVELOPES / name
        self.assertTrue(path.is_file(), f"fixture missing: {path}")
        body = path.read_bytes()
        self.assertTrue(body, f"fixture empty: {path}")
        return body

    def _fixture_text(self, name):
        body = self._fixture_bytes(name).decode("utf-8")
        self.assertTrue(body.strip(), f"fixture empty: {name}")
        return body

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


class ArgvAsksForTheEnvelopeByConstruction(_U10Launch):
    def test_claude_fresh_argv_carries_json_output_and_json_schema(self):
        """Scenario: claude fresh argv asks for json output and the envelope schema"""
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        argv = self._argv_list(witness)
        self.assertIn("--output-format", argv)
        self.assertEqual(
            argv[argv.index("--output-format") + 1], "json",
            f"claude must ask for json output, argv={argv!r}",
        )
        value = self._schema_value(argv)
        schema = self._loaded_schema(value)
        self.assertEqual(schema.get("type"), "object")
        self._child_schema_reachable(witness, value)
        self.assertEqual(rec["harness"]["name"], "claude")

    def test_claude_resume_argv_keeps_json_output_and_json_schema(self):
        """Scenario: claude resume argv keeps json output and the envelope schema"""
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        argv = self._argv_list(self._resume_witness(rec))
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self._schema_value(argv)
        self.assertTrue("-r" in argv or "--resume" in argv or "-p" in argv
                        or "--print" in argv)

    def test_grok_fresh_argv_does_not_ask_for_a_schema(self):
        """Scenario: grok keeps plain output — `--json-schema` short-circuits the loop

        Measured 2026-08-29 (record 20260829T234838Z-review-grok-66ccb2,
        grok 1.0.5): with `--json-schema` grok returned a schema-valid,
        empty envelope after one model call and ended the turn. Replay:
        fixtures/envelopes/grok-json-schema-short-circuit-66ccb2.raw.out.
        """
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        argv = self._argv_list(witness)
        self.assertNotIn("--json-schema", argv, f"grok argv={argv!r}")
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "plain")
        self.assertEqual(rec["harness"]["name"], "grok")

    def test_grok_resume_argv_keeps_plain_output(self):
        """Scenario: grok resume argv keeps plain output and no schema"""
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        argv = self._argv_list(self._resume_witness(rec))
        self.assertNotIn("--json-schema", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "plain")
        self.assertIn("-r", argv)
        self.assertIn(
            "--model", argv,
            "D-RES-1: resume argv is the fresh argv with the resume "
            f"flag substituted; --model must stay: argv={argv!r}",
        )
        self.assertEqual(
            argv[argv.index("--model") + 1], ls.REQUESTED_MODEL,
            f"resume must keep the pinned model: argv={argv!r}",
        )
        self.assertIn(
            "--prompt-file", argv,
            "D-RES-1: grok resume must still pass --prompt-file: "
            f"argv={argv!r}",
        )

    def test_the_captured_short_circuit_replay_is_a_valid_but_empty_envelope(self):
        """Scenario: the launcher's scan accepts the captured bytes — and that is the problem the argv change answers"""
        body = self._fixture_text("grok-json-schema-short-circuit-66ccb2.raw.out")
        planted = json.loads(body)
        nested = planted.get("structuredOutput")
        self.assertIsInstance(nested, dict, "plant: nested envelope present")
        self.assertEqual(nested.get("status"), "ok", "plant: empty ok envelope")
        self.assertEqual(nested.get("findings"), [], "plant: no findings")
        self.assertIn("starting", str(nested.get("note")))
        self.assertGreater(
            len(planted), 9,
            "plant: the wrapper is not itself a nine-key envelope",
        )
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body.rstrip("\n")
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"structuredOutput", data, "plant: fixture replayed")
        env = _envelope(rec)
        self.assertEqual(
            env.get("status"), "ok",
            "the scan accepts the captured short-circuit bytes — that "
            f"is why grok must not be launched with --json-schema: {env!r}",
        )
        self.assertEqual(env.get("findings"), [], env)
        self.assertIn("starting", str(env.get("note")))

    def test_codex_fresh_argv_carries_json_and_last_message_file(self):
        """Scenario: codex fresh argv asks for json events and a last-message file"""
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="codex", stage="plan",
        )
        argv = self._argv_list(witness)
        self.assertIn("--json", argv, f"codex argv={argv!r}")
        self.assertIn("-o", argv, f"codex argv={argv!r}")
        dest = Path(argv[argv.index("-o") + 1])
        expected = Path(rec["snapshot"]["root"]).parent / "out" / (
            "last-message.json"
        )
        self.assertEqual(
            dest.resolve(), expected.resolve(),
            f"-o must be <job>/out/last-message.json, got {dest}",
        )

    def test_codex_resume_argv_keeps_json_and_last_message_file(self):
        """Scenario: codex resume argv keeps json events and the last-message file"""
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        argv = self._argv_list(self._resume_witness(rec))
        self.assertIn("--json", argv, f"codex resume argv={argv!r}")
        self.assertIn("-o", argv, f"codex resume argv={argv!r}")
        dest = Path(argv[argv.index("-o") + 1])
        expected = Path(rec["snapshot"]["root"]).parent / "out" / (
            "last-message.json"
        )
        self.assertEqual(dest.resolve(), expected.resolve())
        self.assertIn("resume", argv)

    def test_argv_schema_is_the_exported_envelope_schema(self):
        """Scenario: claude argv schema is the exported envelope schema"""
        _rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        supplied = self._loaded_schema(
            self._schema_value(self._argv_list(witness)),
        )
        self.assertEqual(
            supplied, self._exported_schema(),
            "the schema handed to the child must be envelope.ENVELOPE_SCHEMA",
        )

    def test_the_child_reads_the_json_schema_value(self):
        """Scenario: the child reads the --json-schema value from its own argv"""
        _rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        value = self._schema_value(self._argv_list(witness))
        self._child_schema_reachable(witness, value)
        self.assertEqual(
            self._loaded_schema(value), self._exported_schema(),
        )

    def test_claude_argv_schema_is_the_exported_envelope_schema(self):
        """Scenario: claude argv schema is the exported envelope schema"""
        _rec, witness, *_ = self.launch_ok(
            job="plan", harness="claude", stage="plan",
        )
        supplied = self._loaded_schema(
            self._schema_value(self._argv_list(witness)),
        )
        self.assertEqual(
            supplied, self._exported_schema(),
            "claude --json-schema must be envelope.ENVELOPE_SCHEMA, "
            "not a stale or grok-only copy",
        )


class ClaudeWrapperIsTheFirstEnvelopeSource(_U10Launch):
    def test_structured_output_is_the_envelope(self):
        """Scenario: a claude wrapper with structured_output is unwrapped as the envelope"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-claude-structured"
        os.environ["TASK_LAUNCH_WRAPPER_DECOY"] = "u10-decoy-scan"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-claude-structured", data)
        self.assertIn(b"u10-decoy-scan", data)
        self.assertGreater(
            data.rfind(b"u10-decoy-scan"),
            data.find(b"u10-claude-structured"),
            "plant: decoy is the last JSON object on stdout",
        )
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-claude-structured",
            "structured_output is the first source; a later scan-shaped "
            f"object must not win: envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_result_string_is_parsed_when_structured_output_is_absent(self):
        """Scenario: a claude wrapper with the envelope only in result text is unwrapped"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "result"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-claude-result-string"
        os.environ["TASK_LAUNCH_WRAPPER_DECOY"] = "u10-decoy-scan"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        first = data.decode("utf-8").splitlines()[0]
        wrapper = json.loads(first)
        self.assertEqual(wrapper.get("type"), "result", wrapper)
        self.assertNotIn(
            "structured_output", wrapper,
            "plant: this case has no structured_output field",
        )
        parsed = json.loads(wrapper["result"])
        self.assertEqual(parsed.get("note"), "u10-claude-result-string")
        self.assertIn(b"u10-decoy-scan", data)
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-claude-result-string",
            "the result JSON string is the first source when "
            f"structured_output is absent: envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_wrapper_without_envelope_is_claude_result_subtype(self):
        """Scenario: a claude wrapper with no envelope is claude-result subtype"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "none"
        os.environ["TASK_LAUNCH_WRAPPER_SUBTYPE"] = "error_max_turns"
        os.environ["TASK_LAUNCH_WRAPPER_IS_ERROR"] = "1"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8"))
        self.assertEqual(wrapper.get("type"), "result")
        self.assertEqual(wrapper.get("subtype"), "error_max_turns")
        self.assertTrue(wrapper.get("is_error"))
        self.assertNotIsInstance(wrapper.get("structured_output"), dict)
        self.assertEqual(
            _cause_reason(rec), "claude-result:error_max_turns",
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )
        self.assertEqual(_envelope(rec).get("status"), "invalid")

    def test_is_error_wins_over_structured_output(self):
        """Scenario: a claude wrapper with is_error true is claude-result even when structured_output is present"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_WRAPPER_SUBTYPE"] = "error_max_turns"
        os.environ["TASK_LAUNCH_WRAPPER_IS_ERROR"] = "1"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-truncated-structured"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8").splitlines()[0])
        self.assertTrue(wrapper.get("is_error"), "plant: is_error landed")
        self.assertIsInstance(
            wrapper.get("structured_output"), dict,
            "plant: structured_output is present beside is_error",
        )
        self.assertEqual(
            _cause_reason(rec), "claude-result:error_max_turns",
            "is_error is the ending; structured_output written before "
            "the wall must not close as ok: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )
        self.assertEqual(_envelope(rec).get("status"), "invalid")

    def test_replayed_error_max_turns_fixture_is_claude_result_subtype(self):
        """Scenario: a claude wrapper with no envelope is claude-result subtype"""
        body = self._fixture_text(CLAUDE_MAX_TURNS_FIXTURE)
        planted = json.loads(body)
        self.assertEqual(planted.get("subtype"), "error_max_turns")
        self.assertTrue(planted.get("is_error"))
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        replayed = json.loads(data.decode("utf-8"))
        self.assertEqual(replayed.get("subtype"), "error_max_turns")
        self.assertTrue(replayed.get("is_error"))
        self.assertEqual(
            _cause_reason(rec), "claude-result:error_max_turns",
            f"replayed docs-shaped wrapper must name the subtype: "
            f"cause={ (rec.get('result') or {}).get('cause')!r}",
        )


class GrokObjectIsTheEnvelope(_U10Launch):
    def test_stdout_object_matching_schema_is_the_envelope(self):
        """Scenario: a grok trailing nested-invalid object loses to an earlier valid envelope"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-grok-object"
        os.environ["TASK_LAUNCH_WRAPPER_DECOY"] = "u10-decoy-scan"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-grok-object", data)
        self.assertIn(b"u10-decoy-scan", data)
        self.assertGreater(
            data.find(b"u10-decoy-scan"),
            data.find(b"u10-grok-object"),
            "plant: decoy trails the harness object on stdout",
        )
        objects = [
            json.loads(ln) for ln in data.decode("utf-8").splitlines()
            if ln.strip().startswith("{")
        ]
        self.assertGreaterEqual(len(objects), 2, "plant: object then decoy")
        self.assertEqual(objects[0].get("note"), "u10-grok-object")
        self.assertEqual(objects[-1].get("note"), "u10-decoy-scan")
        self.assertEqual(
            objects[-1].get("counts", {}).get("p1"), "scan",
            "plant: trailing decoy is scan-shaped with nested wrong type",
        )
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-grok-object",
            "D-ENV-2: grok's envelope is the last fully-valid object; a "
            "trailing scan-shaped decoy (nested wrong type) must lose: "
            f"envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)
        self.assertNotEqual(_cause_reason(rec), "schema-invalid", rec)

    def test_null_verdict_on_invalid_is_not_schema_invalid(self):
        """Scenario: a legal envelope with a null verdict is not schema-invalid"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        os.environ["TASK_LAUNCH_STATUS"] = "invalid"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-null-verdict"
        os.environ["TASK_LAUNCH_WRAPPER_DECOY_FIRST"] = "u10-decoy-first"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-decoy-first", data, "plant: valid decoy first")
        self.assertIn(b"u10-null-verdict", data)
        self.assertLess(
            data.find(b"u10-decoy-first"),
            data.find(b"u10-null-verdict"),
            "plant: fully-valid decoy precedes the allowed envelope",
        )
        text = data.decode("utf-8")
        objects = [
            json.loads(ln) for ln in text.splitlines()
            if ln.strip().startswith("{")
        ]
        self.assertEqual(len(objects), 2, "plant: decoy then envelope")
        self.assertEqual(objects[0].get("note"), "u10-decoy-first")
        planted = objects[1]
        self.assertIsNone(planted.get("verdict"), "plant: verdict is null")
        self.assertEqual(planted.get("status"), "invalid")
        self.assertNotIn("commit", planted, "plant: commit is absent")
        self.assertNotEqual(
            _cause_reason(rec), "schema-invalid",
            "verdict null on invalid is allowed (D-ENV-1); a validator "
            "that rejects it closes every invalid envelope: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )
        env = _envelope(rec)
        self.assertEqual(env.get("note"), "u10-null-verdict", env)
        self.assertIsNone(env.get("verdict"), env)

    def test_optional_commit_is_not_schema_invalid(self):
        """Scenario: a legal envelope with optional commit is not schema-invalid"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_ENVELOPE_COMMIT"] = "1"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-with-commit"
        os.environ["TASK_LAUNCH_WRAPPER_DECOY_FIRST"] = "u10-decoy-first"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-decoy-first", data, "plant: valid decoy first")
        self.assertIn(b"u10-with-commit", data)
        self.assertLess(
            data.find(b"u10-decoy-first"),
            data.find(b"u10-with-commit"),
            "plant: fully-valid decoy precedes the allowed envelope",
        )
        objects = [
            json.loads(ln) for ln in data.decode("utf-8").splitlines()
            if ln.strip().startswith("{")
        ]
        self.assertEqual(len(objects), 2, "plant: decoy then envelope")
        self.assertEqual(objects[0].get("note"), "u10-decoy-first")
        self.assertNotIn("commit", objects[0], "plant: first decoy has no commit")
        planted = objects[1]
        self.assertIn("commit", planted, "plant: commit landed")
        self.assertEqual(planted["commit"].get("subject"), "dispatch: pin U10")
        self.assertNotEqual(
            _cause_reason(rec), "schema-invalid",
            "commit is optional and allowed when present: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )
        env = _envelope(rec)
        self.assertEqual(env.get("note"), "u10-with-commit", env)
        self.assertEqual(env.get("status"), "ok", env)
        self.assertIn("commit", env, env)

    def test_object_with_an_extra_key_is_schema_invalid(self):
        """Scenario: a grok stdout object that fails the schema is schema-invalid"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_WRAPPER_INVALID"] = "extra"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-grok-extra"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        planted = json.loads(data.decode("utf-8"))
        self.assertIn("transcript", planted, "plant: extra key landed")
        self.assertEqual(planted.get("note"), "u10-grok-extra")
        self.assertEqual(
            _cause_reason(rec), "schema-invalid",
            "an object that fails ENVELOPE_SCHEMA is schema-invalid, "
            "not envelope-parse or envelope-missing: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )
        self.assertEqual(_envelope(rec).get("status"), "invalid")

    def test_object_with_a_missing_key_is_schema_invalid(self):
        """Scenario: a grok stdout object that fails the schema is schema-invalid"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_WRAPPER_INVALID"] = "missing"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        planted = json.loads(data.decode("utf-8"))
        self.assertNotIn("note", planted, "plant: note stripped")
        self.assertEqual(
            _cause_reason(rec), "schema-invalid",
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )

    def test_narration_then_object_still_parses_via_the_fallback_scan(self):
        """Scenario: a grok narration-then-object raw.out still parses via the fallback scan"""
        body = self._fixture_text(GROK_NARRATION_FIXTURE)
        brace = body.find("{")
        self.assertGreater(brace, 0, "plant: object begins mid-line")
        self.assertNotEqual(body[brace - 1], "\n")
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-grok-narration-then-object", data)
        env = _envelope(rec)
        note = str(env.get("note") or "")
        self.assertNotIn("envelope-parse", note.lower(), env)
        self.assertEqual(
            env.get("note"), "u10-grok-narration-then-object", env,
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_fallback_scan_object_that_fails_the_schema_is_schema_invalid(self):
        """Scenario: a grok fallback-scan object that fails the schema is schema-invalid"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "grok"
        os.environ["TASK_LAUNCH_WRAPPER_NARRATION"] = (
            "working, then the object."
        )
        os.environ["TASK_LAUNCH_WRAPPER_INVALID"] = "extra"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-grok-fallback-extra"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"working, then the object.", data)
        text = data.decode("utf-8")
        planted = json.loads(text[text.find("{"):])
        self.assertIn("transcript", planted, "plant: extra key landed")
        self.assertEqual(
            _cause_reason(rec), "schema-invalid",
            "the fallback scan must still validate: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )

    def test_captured_s2_raw_out_recovers_the_object(self):
        """Scenario: a captured grok S2 raw.out recovers the object instead of envelope-parse"""
        body = self._fixture_bytes(GROK_S2_FIXTURE)
        brace = body.find(b"{")
        self.assertGreater(brace, 0, "plant: object begins mid-line")
        self.assertNotEqual(body[brace - 1], 10, "plant: no newline before {")
        planted = json.loads(body[brace:])
        self.assertEqual(planted.get("job"), "author-tests")
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body.decode("utf-8")
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertEqual(data.rstrip(b"\n"), body.rstrip(b"\n"))
        env = _envelope(rec)
        blob = str(env.get("note") or "") + str(_cause_reason(rec) or "")
        self.assertNotIn(
            "envelope-parse", blob.lower(),
            "D-ENV-1: today's raw.out still parses; 13f5f2's object is "
            f"on stdout: note={env.get('note')!r} "
            f"cause={ (rec.get('result') or {}).get('cause')!r}",
        )
        self.assertEqual(
            env.get("job"), "author-tests",
            f"the captured object is the envelope: {env!r}",
        )


class CodexLastMessageFileIsTheFirstEnvelopeSource(_U10Launch):
    def test_envelope_is_read_from_the_dash_o_file(self):
        """Scenario: a codex last-message file is the first envelope source"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "file"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-file"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertTrue(last.is_file(), f"plant: {last} must exist")
        planted = json.loads(last.read_text(encoding="utf-8"))
        self.assertEqual(planted.get("note"), "u10-codex-file")
        _raw, data = self._raw_out()
        self.assertIn(b"thread.started", data)
        self.assertNotIn(b"u10-codex-file", data)
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-codex-file",
            f"the -o file is the first source: envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_file_wins_over_a_disagreeing_agent_message(self):
        """Scenario: a codex last-message file wins over a disagreeing agent_message"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "both"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-file-wins"
        os.environ["TASK_LAUNCH_WRAPPER_AGENT_NOTE"] = "u10-codex-agent-loses"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertTrue(last.is_file(), f"plant: {last} must exist")
        planted = json.loads(last.read_text(encoding="utf-8"))
        self.assertEqual(planted.get("note"), "u10-codex-file-wins")
        _raw, data = self._raw_out()
        self.assertIn(b"u10-codex-agent-loses", data)
        self.assertIn(b"agent_message", data)
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-codex-file-wins",
            "file then agent_message then stdout: the file wins when "
            f"both disagree: envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_last_agent_message_is_used_when_the_file_is_absent(self):
        """Scenario: a codex last agent_message is used when the file is absent"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "agent_message"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-agent-message"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertFalse(
            last.is_file(),
            f"plant: the -o file must be absent, found {last}",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-codex-agent-message", data)
        self.assertIn(b"agent_message", data)
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-codex-agent-message",
            f"last agent_message is the second source: envelope={env!r}",
        )
        self.assertEqual(env.get("status"), "ok", env)

    def test_replayed_ndjson_fixture_unwraps_the_agent_message(self):
        """Scenario: a codex last agent_message is used when the file is absent"""
        body = self._fixture_text(CODEX_NDJSON_FIXTURE)
        self.assertIn("agent_message", body)
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body.rstrip("\n")
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-codex-agent-message", data)
        env = _envelope(rec)
        self.assertEqual(
            env.get("note"), "u10-codex-agent-message",
            f"replayed NDJSON tail must unwrap agent_message: {env!r}",
        )

    def test_absent_file_and_no_agent_message_is_no_last_message(self):
        """Scenario: a missing codex last-message file with nothing else is no-last-message"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "none"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertFalse(last.is_file(), f"plant: file absent, found {last}")
        _raw, data = self._raw_out()
        self.assertIn(b"thread.started", data)
        self.assertNotIn(b'"job"', data)
        self.assertEqual(
            _cause_reason(rec), "no-last-message",
            "absent -o file and no agent_message is no-last-message, "
            "not envelope-parse: "
            f"cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )

    def test_resume_clears_the_previous_last_message_file(self):
        """Scenario: a resumed codex attempt clears the previous last-message file"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "file"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-attempt-1"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertTrue(last.is_file(), "plant: attempt 1 wrote the file")
        first = json.loads(last.read_text(encoding="utf-8"))
        self.assertEqual(first.get("note"), "u10-codex-attempt-1")
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-attempt-2"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "agent_message"
        self.start_witness.unlink(missing_ok=True)
        self.witness.unlink()
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, self.combined(out, err))
        started = self.read_start_witness()
        self.assertFalse(
            started.get("last_message_present"),
            "resume must clear out/last-message.json before the child "
            "starts, else a file-first reader returns attempt 1: "
            f"start-witness={started!r}",
        )
        rec2 = self.read_record()
        env = _envelope(rec2)
        self.assertEqual(
            env.get("note"), "u10-codex-attempt-2",
            "attempt 2's envelope, not the leftover file: "
            f"envelope={env!r}",
        )

    def test_last_message_file_is_written_on_nonzero_exit(self):
        """Scenario: a codex last-message file is written on a non-zero exit"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "codex"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "file"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-codex-nonzero"
        os.environ["TASK_LAUNCH_EXIT"] = "1"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="codex", stage="plan",
        )
        job_dir = Path(rec["snapshot"]["root"]).parent
        last = job_dir / "out" / "last-message.json"
        self.assertTrue(
            last.is_file(),
            "premise: -o is written on a non-zero exit; "
            f"missing {last}",
        )
        planted = json.loads(last.read_text(encoding="utf-8"))
        self.assertEqual(planted.get("note"), "u10-codex-nonzero")
        reason = str(_cause_reason(rec) or "")
        self.assertTrue(
            reason.startswith("harness-cli:"),
            "non-zero exit is harness-cli:<n>, not an ok envelope from "
            f"the file: cause={ (rec.get('result') or {}).get('cause')!r} "
            f"envelope={_envelope(rec)!r}",
        )
        note = str(_envelope(rec).get("note") or "")
        self.assertIn(
            "u10-codex-nonzero", note,
            "the harness note is present beside the exit: "
            f"envelope={_envelope(rec)!r}",
        )
        self.assertIn(
            "harness-cli:", note,
            "a non-zero exit must name itself in the envelope note "
            f"(not only in result.cause): note={note!r}",
        )


class EnvelopeParseOnlyWhenNothingParsed(_U10Launch):
    def test_prose_stdout_is_still_envelope_parse(self):
        """Scenario: envelope-parse remains only when nothing at all parsed"""
        os.environ["TASK_LAUNCH_STDOUT"] = "prose"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="grok", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertNotIn(b"{", data, "plant: prose has no JSON object")
        env = _envelope(rec)
        note = str(env.get("note") or "")
        blob = note + str(_cause_reason(rec) or "")
        self.assertIn(
            "envelope-parse", blob.lower(),
            "prose with no JSON object is envelope-parse, not a wrapper "
            f"or schema cause: note={note!r} "
            f"cause={ (rec.get('result') or {}).get('cause')!r}",
        )
        self.assertEqual(env.get("status"), "invalid")
        reason = str(_cause_reason(rec) or "")
        self.assertFalse(
            reason.startswith("claude-result:"),
            f"prose is not a claude wrapper: cause={reason!r}",
        )
        self.assertNotEqual(reason, "schema-invalid")
        self.assertNotEqual(reason, "no-last-message")


class StampRefIsOverwrittenOnUnwrap(_U10Launch):
    def test_wrapper_stamp_ref_is_replaced_with_the_snapshot_sha(self):
        """Scenario: the launcher overwrites stamp.ref on an unwrapped envelope"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-stamp-overwrite"
        os.environ["TASK_LAUNCH_WRAPPER_DECOY"] = "u10-decoy-scan"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        self.assertIn(b"u10-stamp-overwrite", data)
        self.assertIn(b'"ref": "harness-placeholder"', data)
        env = _envelope(rec)
        self.assertEqual(env.get("note"), "u10-stamp-overwrite", env)
        stamp = env.get("stamp") or {}
        self.assertEqual(
            stamp.get("ref"), rec["snapshot"]["ref_sha"],
            f"launcher overwrites stamp.ref: stamp={stamp!r}",
        )
        self.assertNotEqual(stamp.get("ref"), "harness-placeholder")


class ClaudeWrapperSpendIsSessionSpend(_U10Launch):
    def test_wrapper_usage_is_session_spend(self):
        """Scenario: claude wrapper usage is session.spend"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "structured_output"
        os.environ["TASK_LAUNCH_NOTE"] = "u10-spend-from-wrapper"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "0"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        _raw, data = self._raw_out()
        wrapper = json.loads(data.decode("utf-8").splitlines()[0])
        self.assertEqual(wrapper.get("usage", {}).get("input_tokens"), 10)
        self.assertEqual(wrapper.get("total_cost_usd"), 0.001)
        spend = _spend(rec)
        self.assertIsInstance(spend, dict, spend)
        self.assertNotIn("unresolved", spend, spend)
        self.assertEqual(spend.get("input"), 10, spend)
        self.assertEqual(spend.get("output"), 4, spend)
        self.assertEqual(spend.get("cost_usd"), 0.001, spend)
        self.assertEqual(spend.get("source"), "result.usage", spend)

    def test_claude_result_still_records_wrapper_spend(self):
        """Scenario: claude-result still records session.spend from the wrapper"""
        os.environ["TASK_LAUNCH_WRAPPER"] = "claude"
        os.environ["TASK_LAUNCH_WRAPPER_FIELD"] = "none"
        os.environ["TASK_LAUNCH_WRAPPER_SUBTYPE"] = "error_max_turns"
        os.environ["TASK_LAUNCH_WRAPPER_IS_ERROR"] = "1"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "0"
        rec, *_ = self._dispatch_closed(
            job="plan", harness="claude", stage="plan",
        )
        self.assertEqual(_cause_reason(rec), "claude-result:error_max_turns")
        spend = _spend(rec)
        self.assertIsInstance(spend, dict, spend)
        self.assertNotIn("unresolved", spend, spend)
        self.assertEqual(spend.get("input"), 10, spend)
        self.assertEqual(spend.get("output"), 4, spend)
        self.assertEqual(spend.get("cost_usd"), 0.001, spend)
        self.assertEqual(spend.get("source"), "result.usage", spend)
