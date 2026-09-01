"""Close must find a complete envelope that begins mid-line on stdout.

Written from records:

  20260828T220752Z-review-grok-bf0679 (repo/self-hosted-runners)
  20260828T221053Z-review-grok-4c887d (dispatch/prompt-feed)

Both closed ``envelope-parse: no JSON object on stdout`` while each
``raw.out`` holds a complete single-line envelope that begins mid-line,
directly after the final narration sentence, with no newline before
its opening brace.

4c887d p3: a string stamp is stored under ``stamp.model``, so a SHA is
labelled as a model id. Envelope stamp fields are ``ref``, ``started``,
``ended`` (task CONTRACT.md §The envelope); a SHA is a ref, not a model.
"""

from __future__ import annotations

import json
import os

import launch_support as ls

# Distinct from any snapshot ref the launcher fills in. 40 hex chars.
STRING_STAMP_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

# Final narration sentences from the two records, immediately followed
# by the envelope's opening brace in each raw.out.
BF0679_NARRATION = (
    "I'll reproduce the suspected defects against the tree before "
    "recording any finding."
)
C887D_NARRATION = (
    "I'm going to verify the highest-severity claims with commands so "
    "each finding has a reproduction, not a recollection."
)

BF0679_NOTE = "bf0679-midline-self-hosted-runners"
C887D_NOTE = "4c887d-midline-prompt-feed"


def _ok_envelope(*, note, stamp=None):
    if stamp is None:
        stamp = {
            "ref": "harness-placeholder",
            "started": None,
            "ended": None,
        }
    return {
        "job": "plan",
        "status": "ok",
        "verdict": "changes",
        "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
        "findings": [],
        "artifacts": {},
        "spend": {"harness": "grok", "total": 0, "out": 0, "runs": 1},
        "stamp": stamp,
        "note": note,
    }


class MidlineEnvelopeOnStdoutIsParsed(ls._TempLaunch):
    """A complete JSON envelope that starts mid-line is still an envelope.

    The two closed reviews emitted narration and then the object on the
    same line. ``envelope-parse: no JSON object on stdout`` is the
    wrong close for that stdout.
    """

    def _close_with_stdout(self, body):
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
        ))
        text = self.combined(out, err)
        self.assertNotEqual(
            code, ls.REFUSAL_EXIT,
            f"close of a finished job must not refuse: {text!r}",
        )
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed")
        return rec, self.the_job_dir() / "raw.out"

    def _assert_midline_plant(self, raw, *, narration, envelope):
        """Prove the stdout shape the two records actually had."""
        self.assertTrue(raw.is_file(), "raw.out is missing")
        data = raw.read_bytes()
        self.assertTrue(data, "plant: raw.out is empty")
        self.assertIn(
            narration.encode("utf-8"), data,
            "plant: narration must be in raw.out",
        )
        brace = data.find(b"{")
        self.assertGreater(
            brace, 0,
            "plant: envelope must begin mid-line, not at byte 0",
        )
        self.assertNotEqual(
            data[brace - 1], 0x0A,
            "plant: no newline before the opening brace "
            f"(byte before '{{' is {data[brace - 1]!r})",
        )
        prefix = data[:brace]
        self.assertTrue(
            prefix.endswith(narration.encode("utf-8")),
            "plant: the brace sits directly after the narration "
            f"sentence; prefix={prefix[-80:]!r}",
        )
        parsed = json.loads(data[brace:])
        self.assertEqual(
            parsed, envelope,
            "plant: raw.out must hold the complete planted envelope",
        )
        self.assertEqual(
            parsed.get("note"), envelope["note"],
            "plant: unique note must survive into the JSON object",
        )

    def _assert_not_envelope_parse(self, rec, *, unique_note):
        envelope = (rec.get("result") or {}).get("envelope") or {}
        note = envelope.get("note")
        note_text = str(note or "")
        # Stated reason first: this is the close those two records got.
        self.assertNotIn(
            "no JSON object on stdout",
            note_text,
            "raw.out holds a complete JSON envelope; close must not "
            f"report envelope-parse: note={note!r} envelope={envelope!r}",
        )
        self.assertNotIn(
            "envelope-parse",
            note_text.lower(),
            "a mid-line envelope is not envelope-parse: "
            f"note={note!r} envelope={envelope!r}",
        )
        self.assertNotEqual(
            envelope.get("status"), "invalid",
            "parsed stdout is not an invalid close: "
            f"envelope={envelope!r}",
        )
        self.assertEqual(
            envelope.get("note"), unique_note,
            "the planted envelope must be the one on the record, not "
            f"a reconstructed stand-in: envelope={envelope!r}",
        )

    def test_self_hosted_runners_review_midline_envelope_is_parsed(self):
        """Record 20260828T220752Z-review-grok-bf0679."""
        envelope = _ok_envelope(note=BF0679_NOTE)
        body = BF0679_NARRATION + json.dumps(envelope, separators=(",", ":"))
        rec, raw = self._close_with_stdout(body)
        self._assert_midline_plant(
            raw, narration=BF0679_NARRATION, envelope=envelope,
        )
        self._assert_not_envelope_parse(rec, unique_note=BF0679_NOTE)

    def test_prompt_feed_review_midline_envelope_is_parsed(self):
        """Record 20260828T221053Z-review-grok-4c887d."""
        envelope = _ok_envelope(note=C887D_NOTE)
        body = C887D_NARRATION + json.dumps(envelope, separators=(",", ":"))
        rec, raw = self._close_with_stdout(body)
        self._assert_midline_plant(
            raw, narration=C887D_NARRATION, envelope=envelope,
        )
        self._assert_not_envelope_parse(rec, unique_note=C887D_NOTE)


class StringStampIsNotAModelId(ls._TempLaunch):
    """4c887d p3 — launch.py ``_envelope``.

    A string stamp (a SHA) stored under ``stamp.model`` labels a commit
    as a model id. Stamp fields are ref / started / ended.
    """

    def test_a_string_stamp_sha_is_not_stored_under_stamp_model(self):
        envelope = _ok_envelope(
            note="string-stamp-is-a-sha",
            stamp=STRING_STAMP_SHA,
        )
        self.assertIsInstance(envelope["stamp"], str)
        self.assertEqual(envelope["stamp"], STRING_STAMP_SHA)
        body = json.dumps(envelope, separators=(",", ":"))
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        os.environ["TASK_LAUNCH_TOKEN"] = body
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
        ))
        text = self.combined(out, err)
        self.assertNotEqual(
            code, ls.REFUSAL_EXIT,
            f"close of a finished job must not refuse: {text!r}",
        )
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed")

        raw = self.the_job_dir() / "raw.out"
        self.assertTrue(raw.is_file(), "raw.out is missing")
        planted = json.loads(raw.read_text(encoding="utf-8"))
        self.assertEqual(
            planted.get("stamp"), STRING_STAMP_SHA,
            "plant: stdout stamp must be the SHA string, "
            f"got {planted.get('stamp')!r}",
        )
        self.assertIsInstance(
            planted.get("stamp"), str,
            "plant: stamp on stdout is a string, not an object",
        )
        self.assertNotEqual(
            STRING_STAMP_SHA, rec["snapshot"]["ref_sha"],
            "plant: the SHA string must differ from snapshot.ref_sha "
            "so a copied ref cannot satisfy the assertion",
        )

        result_env = (rec.get("result") or {}).get("envelope") or {}
        stamp = result_env.get("stamp")
        self.assertIsInstance(
            stamp, dict,
            f"recorded stamp is an object, got {stamp!r}",
        )
        # Stated reason: a SHA labelled as a model id.
        self.assertNotEqual(
            stamp.get("model"), STRING_STAMP_SHA,
            "a string stamp that is a SHA must not be stored under "
            f"stamp.model (a SHA is not a model id): stamp={stamp!r}",
        )
