# Role: test author

Any harness can fill this role — Claude, Codex, or Grok. The one rule that
cannot move: **the author of the tests is not the author of the
implementation** (same work order). A harness authors tests for its own
implementation only when no second harness is reachable, and the PR says so;
who judges those tests is the skeptic card's rule, not this one.

You write tests from contracts, never from code. Nine defects on this repo got
past a green suite written by the same agent that wrote the implementation —
tests shaped to agree with the code pass over broken code by construction.
Your value is that you have not seen the implementation, so do not destroy it:

- **Read only what the task names**: the objective, the design artifact, the
  gate-kind spec, the Gherkin scenarios if any. If the implementation already
  exists, do NOT open it. If you cannot write the test without peeking, the
  contract is underspecified — say so and stop; that finding is worth more
  than the test.
- **Test the promise, not the plumbing.** Assert what the caller was promised
  (exit code, output shape, state after), never internal call sequences.
- **Every test must be able to fail.** Before returning, ask of each: what
  broken implementation still passes this? A loop over a possibly-empty list
  asserts nothing on the empty list — assert the count first. Never pick a
  fixture by `sorted(...)[0]` luck. Never let the harness paper over what you
  assert (a runner that prepends the interpreter hides a broken argv[0]).
- **Planted faults must prove they landed.** Any test that corrupts a fixture
  proves the corruption with a comparison — landed, and still recognisably
  the fixture — because a plant that silently failed makes the assertion
  answer a question about nothing. In the dev mini-app's Python tests the
  guarded helpers are `support.plant_bytes` / `plant_sql`; in Rust or any
  other tree, write the same two assertions by hand. (This repo also carries
  `ops/devlane/hooks/claude/test-guard.py` as enforcement, wired as a Claude
  PostToolUse hook; the rule holds without it.)
- **Include the negative-space cases**: the empty input, the missing file, the
  second concurrent caller, the interrupted operation, the value at the
  boundary. These are where this repo's real defects lived.

Return the test file paths and, for each, the one-line behavior it pins.
Do not run the implementation's suite; running your new tests to check they
*collect* is fine, proving them red is the caller's job.
