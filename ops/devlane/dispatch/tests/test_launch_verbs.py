"""Verbs, jobs root default, template values, input order, id collision.

Written from CONTRACT.md §Dispatch Verbs and Template values.

  V1  DISPATCH_JOBS unset uses XDG_STATE_HOME then HOME/.local/state
  V2  scope cap is bytes, not characters (multibyte)
  V3  template values into/base/diff land; a missing slot is a refusal
  V4  multiple --input keep given order
  V5  a colliding id does not overwrite the first dispatch
"""

from __future__ import annotations

import os
from pathlib import Path

import launch_support as ls


class JobsRootDefaultsToXdgThenHome(ls._TempLaunch):
    """V1 — no flag; $DISPATCH_JOBS, else XDG, else ~/.local/state."""

    def test_xdg_state_home_is_used_when_dispatch_jobs_is_unset(self):
        xdg = self.home / "xdg-state"
        os.environ.pop("DISPATCH_JOBS", None)
        os.environ["XDG_STATE_HOME"] = str(xdg)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        expected = (xdg / "minspec" / "dispatch").resolve()
        root = Path(rec["snapshot"]["root"]).resolve()
        self.assertTrue(
            str(root).startswith(str(expected) + os.sep)
            or root == expected,
            f"jobs root should sit under {expected}, snapshot is {root}",
        )
        prompts = list(expected.rglob("prompt.txt"))
        self.assertTrue(prompts, f"job dir under XDG default is missing: {expected}")

    def test_home_local_state_is_used_when_xdg_is_also_unset(self):
        os.environ.pop("DISPATCH_JOBS", None)
        os.environ.pop("XDG_STATE_HOME", None)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        expected = (
            self.home / ".local" / "state" / "minspec" / "dispatch"
        ).resolve()
        root = Path(rec["snapshot"]["root"]).resolve()
        self.assertTrue(
            str(root).startswith(str(expected) + os.sep)
            or root == expected,
            f"jobs root should sit under {expected}, snapshot is {root}",
        )


class ScopeCapIsBytesNotCharacters(ls._TempLaunch):
    """V2 — 1024 bytes. A 2-byte character must not be counted as 1."""

    def test_five_hundred_and_twelve_e_acute_is_exactly_1024_bytes(self):
        scope = "é" * 512
        self.assertEqual(len(scope.encode("utf-8")), 1024)
        self.assertEqual(len(scope), 512)
        rec, *_ = self.launch_ok(self.argv_for(stage="plan", scope=scope))
        self.assertEqual(rec["brief"]["scope"], scope)
        self.assertEqual(len(rec["brief"]["scope"].encode("utf-8")), 1024)

    def test_one_extra_byte_of_multibyte_scope_is_refused(self):
        scope = "é" * 512 + "x"
        self.assertEqual(len(scope.encode("utf-8")), 1025)
        code, out, err = self.dispatch(self.argv_for(
            stage="plan", scope=scope,
        ))
        self.assert_refusal(
            code, out, err, ident="scope-cap",
            phrases=["scope-cap", "1024"],
        )
        self.assert_not_started()


class TemplateValuesAreSuppliedOrTheRenderIsRefused(ls._TempLaunch):
    """V3 / contract Template values."""

    def test_into_base_and_diff_land_in_the_prompt(self):
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="needs-into", harness="grok", stage="plan",
            scope="template-slots",
        ))
        received = witness.get("prompt_text") or ""
        snap = str(self.snapshot_of(rec).resolve())
        self.assertIn(snap, received, "{into} is the snapshot root")
        self.assertIn(self.ref, received, "{ref} is the named sha")
        self.assertTrue(
            rec["lineage"]["base_sha"] in received
            or rec["snapshot"].get("ref_sha") in received,
            f"{{base}} must land as a sha in the prompt: {received!r}",
        )
        self.assertIn("template-slots", received)

    def test_a_template_value_the_launcher_did_not_supply_is_a_refusal(self):
        code, out, err = self.dispatch(self.argv_for(
            job="needs-hole", harness="grok", stage="plan",
        ))
        text = self.combined(out, err).lower()
        self.assertEqual(code, ls.REFUSAL_EXIT)
        self.assertIn("not_a_slot", text)
        self.assert_not_started()


class MultipleInputsKeepGivenOrder(ls._TempLaunch):
    """V4 — {inputs} is space-separated, in the order given."""

    def test_two_inputs_keep_cli_order_in_the_record_and_the_copies(self):
        first = self.home / "in" / "a-second-alphabetically.md"
        second = self.home / "in" / "b-first-given.md"
        self.plant_new_file(first, "# A\n", must_contain="A")
        self.plant_new_file(second, "# B\n", must_contain="B")
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="check-tests", harness="grok", stage="check-tests",
            extra=["--input", str(second), "--input", str(first)],
        ))
        inputs = rec["brief"]["inputs"]
        self.assertEqual(len(inputs), 2, "both --input flags landed")
        names = [Path(item["path"]).name for item in inputs]
        self.assertEqual(
            names,
            ["b-first-given.md", "a-second-alphabetically.md"],
            "inputs keep given order, not sorted-by-name order",
        )
        job_in = self.the_job_dir() / "in"
        self.assertTrue((job_in / "b-first-given.md").is_file())
        self.assertTrue((job_in / "a-second-alphabetically.md").is_file())
        received = witness.get("prompt_text") or ""
        b_at = received.find("b-first-given.md")
        a_at = received.find("a-second-alphabetically.md")
        self.assertGreaterEqual(b_at, 0)
        self.assertGreaterEqual(a_at, 0)
        self.assertLess(b_at, a_at, "{inputs} is in given order in the prompt")


class ConcurrentIdCollisionIsRefused(ls._TempLaunch):
    """V5 — one directory / one record per id. A colliding mint must
    not overwrite the first dispatch. Seam: launch.mint_id."""

    def test_a_second_dispatch_with_the_same_id_does_not_overwrite(self):
        job_id = "20260826T000000Z-plan-grok-aaaaaa"
        self.force_id(job_id)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["id"], job_id)
        first_bytes = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        self.assertTrue(first_bytes)
        self.start_witness.unlink(missing_ok=True)
        self.witness.unlink(missing_ok=True)
        os.environ["TASK_LAUNCH_TOKEN"] = "SECOND-SHOULD-NOT-RUN"
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        code, out, err = self.dispatch(self.argv_for(
            job="plan", harness="grok", stage="plan",
            scope="a colliding second dispatch",
        ))
        self.assertNotEqual(code, 0, self.combined(out, err))
        after = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        ).read_bytes()
        self.assertEqual(after, first_bytes, "first record must be intact")
        self.assert_not_started()
        self.assertEqual(len(self.job_dirs()), 1)
