"""Named {inputs} and {diff} paths must be readable by a claude child.

Written from CONTRACT.md §Dispatch Verbs lines 65-66 and Template
values lines 138-139.

  --input PATH copies a file into the job directory's in/ and names it
  to the template as {inputs}; {diff} names the diff the launcher writes.

Observed at record 20260828T205636Z-adjudicate-claude-44f652
(repo/ci-burn): the claude child reported in/ as 'outside this session's
allowed working directories', refused every read of {inputs}, and wrote
no ruling. Claude's working directory is snapshot/; in/ and the
launcher-written diff live in the job directory beside it. The child's
allowed working directories are snapshot/ plus every --add-dir.

  C1  every {inputs} path on a claude dispatch is readable by the child
  C2  the {diff} path on a claude dispatch is readable by the child
"""

from __future__ import annotations

import json
from pathlib import Path

import launch_support as ls


def _add_dirs(argv, cwd):
    """Every --add-dir value, resolved the way the child would see it."""
    found = []
    argv = [str(a) for a in argv]
    i = 0
    while i < len(argv):
        a = argv[i]
        raw = None
        if a == "--add-dir" and i + 1 < len(argv):
            raw = argv[i + 1]
            i += 2
        elif a.startswith("--add-dir="):
            raw = a.split("=", 1)[1]
            i += 1
        else:
            i += 1
            continue
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = Path(cwd) / p
        found.append(p.resolve())
    return found


def _under(path, root):
    if path == root:
        return True
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class ClaudeChildCanReadNamedTemplatePaths(ls._TempLaunch):
    """C1 C2 / contract verbs {inputs} and template values {diff}."""

    def setUp(self):
        super().setUp()
        jobs = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        jobs["names-inputs"] = {
            "adapter": "harness",
            "deliverable": "fixture: {inputs} named on its own line",
            "role": "read",
            "snapshot": "whole",
            "prompt": "inputs={inputs}\nAim at: {scope}",
            "constraints": ["read only"],
        }
        jobs["names-diff"] = {
            "adapter": "harness",
            "deliverable": "fixture: {diff} named on its own line",
            "role": "read",
            "snapshot": "whole",
            "prompt": "diff={diff}\nAim at: {scope}",
            "constraints": ["read only"],
        }
        self.jobs_file.write_text(
            json.dumps(jobs, indent=2) + "\n", encoding="utf-8",
        )
        self.ref = self._commit("fixture jobs that name inputs and diff")

    def _prompt_field(self, prompt, key):
        prefix = key + "="
        for line in prompt.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
        self.fail(f"rendered brief has no {key}= line:\n{prompt}")

    def _assert_claude_child(self, witness):
        argv = [str(a) for a in witness["argv"]]
        self.assertTrue(argv, "child argv is empty")
        self.assertEqual(
            Path(argv[0]).name, "claude",
            f"this pin is for a claude child, argv[0]={argv[0]!r}",
        )
        self.assertTrue(witness.get("cwd"), "child cwd is recorded")

    def _assert_readable_by_claude_child(self, path, witness, *, slot):
        cwd = witness["cwd"]
        target = Path(path)
        if not target.is_absolute():
            target = Path(cwd) / target
        target = target.resolve()
        self.assertTrue(
            target.is_file(),
            f"{{{slot}}} must name a file the child can open, got {path!r}",
        )
        roots = [Path(cwd).resolve(), *_add_dirs(witness["argv"], cwd)]
        self.assertTrue(
            any(_under(target, root) for root in roots),
            f"claude child cannot read {{{slot}}} path {target}: "
            f"outside this session's allowed working directories "
            f"(cwd={cwd!r}, add-dir={[str(r) for r in roots[1:]]})",
        )

    def test_every_inputs_path_is_readable_by_the_claude_child(self):
        first = self.home / "src" / "review-beta.md"
        second = self.home / "src" / "review-alpha.md"
        self.plant_new_file(first, "# beta-review\n", must_contain="beta-review")
        self.plant_new_file(
            second, "# alpha-review\n", must_contain="alpha-review",
        )
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="names-inputs", harness="claude", stage="adjudicate",
            extra=["--input", str(first), "--input", str(second)],
        ))
        self.assertEqual(rec["job"], "names-inputs")
        self._assert_claude_child(witness)
        prompt = witness.get("stdin") or ""
        self.assertTrue(prompt, "claude receives the rendered brief on stdin")
        named = self._prompt_field(prompt, "inputs")
        paths = named.split()
        self.assertEqual(
            len(paths), 2,
            f"both --input copies must be named as {{inputs}}, got {named!r}",
        )
        job_in = (self.the_job_dir() / "in").resolve()
        self.assertTrue(job_in.is_dir(), "copies land in the job directory in/")
        seen = []
        for raw in paths:
            copy = Path(raw).resolve()
            self.assertTrue(
                copy.is_file(),
                f"{{inputs}} names {raw!r} which is not a file",
            )
            self.assertEqual(
                copy.parent, job_in,
                f"{{inputs}} names the copy under in/, got {copy}",
            )
            seen.append(copy.name)
        self.assertEqual(
            seen, ["review-beta.md", "review-alpha.md"],
            "{inputs} is the copies in the order given",
        )
        self.assertIn(b"beta-review", (job_in / "review-beta.md").read_bytes())
        self.assertIn(b"alpha-review", (job_in / "review-alpha.md").read_bytes())
        for raw in paths:
            self._assert_readable_by_claude_child(
                Path(raw).resolve(), witness, slot="inputs",
            )

    def test_the_diff_path_is_readable_by_the_claude_child(self):
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job="names-diff", harness="claude", stage="review",
            scope="pin the launcher-written diff",
        ))
        self._assert_claude_child(witness)
        prompt = witness.get("stdin") or ""
        self.assertTrue(prompt, "claude receives the rendered brief on stdin")
        named = self._prompt_field(prompt, "diff").strip()
        job_dir = self.the_job_dir()
        self.assertTrue(
            named,
            "{diff} must name the diff the launcher writes, not a hole "
            f"in the brief; got {named!r} in:\n{prompt}\n"
            f"job dir={[p.name for p in job_dir.iterdir()]}",
        )
        self.assertNotIn(" ", named, f"{{diff}} is one path, got {named!r}")
        self.assertEqual(rec["job"], "names-diff")
        self._assert_readable_by_claude_child(named, witness, slot="diff")
