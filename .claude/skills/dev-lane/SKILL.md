---
name: dev-lane
description: The dev-lane pipeline — plan (Fable), tests (Grok), check the tests (Codex), code (Opus), review the code (Grok AND Codex, both). NO PRODUCER OWNS TWO CONSECUTIVE ARTIFACTS: whoever writes an implementation does not write or approve its tests. Scope is settled in session with the owner, then handed off. Load before starting any change under .dev/, and before dispatching any harness.
---

Read and follow `ops/process/pipeline.md` — the canonical sequence.
`ops/process/cross-review.md` covers review only; reading it alone gives
three roles where there are six.

Three rules that must survive any summary:

- **No producer owns two consecutive artifacts.** The specific models
  matter less than that constraint. Measured twice on this repo: nine
  defects past a green suite whose tests and code shared an author, and
  eleven of fourteen review findings against two checkers being missing
  test cases rather than coding errors.

- **The firewall is proved, not intended.** Withholding is invisible — a
  snapshot that leaked the wrong file looks exactly like one that did
  not. Prove both directions before dispatching: nothing matching a
  withheld pattern present, *and* something matching every given pattern
  present. An empty snapshot satisfies the first perfectly.

- **Contracts are extracted from an app's contract document, never from a
  plan.** That is what keeps a plan disposable; the plan it was learned
  from could not be retired because 1,236 citations under
  `.dev/app/workflow/contracts/` point at it.

Scope is settled in session with the owner; the plan is not. State
*what* and the boundaries, and leave *how* to the planner. Review goes
to two harnesses, not one, so the rule holds whoever is driving — and a
CHANGES verdict is ruled on by a harness that produced neither the
artifact nor the finding, never by the producer, before any of it is
worked.

Reach: Claude loads this natively and Grok through claude-compat. **Codex
does not see repo skills** — its copy of these rules is `AGENTS.md`, which
every harness reads.
