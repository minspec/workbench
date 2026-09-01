# Behavior first, in Gherkin shape

A scenario is a contract a non-implementer can dispute. Write them at the
**spec** stage, before tests exist, so the test-author role has something
to work from that is not the implementation.

## Where they live

`.dev/design/features/<area>/<slug>.feature` — tracked, reviewed in the PR
like any design artifact, and **in the tdd gate kind's `test_scope`**, so
sealing the frozen set at red freezes the scenarios with the tests: changing
a scenario afterwards is manifest drift that `wf green` refuses, and the
recorded route back is `wf advance --to red --reason`. Plain Gherkin syntax;
no Cucumber runtime is wired up — the value extracted here is the shared,
disputable contract, and the mapping below keeps it honest without a
step-definition layer.

## Writing them

```gherkin
Feature: <the promise, one line>
  Scenario: <one observable behavior>
    Given <state that exists before>
    When <the one action under test>
    Then <the observable consequence — exit code, output, state after>
```

- One behavior per scenario; one `When` per scenario. If you need two, it is
  two scenarios.
- `Then` must be observable from outside — an exit code, bytes, a row, a
  refusal message. "The cache is consistent" is not observable; "a second
  launch answers the same work orders" is.
- **Write the negative-space scenarios** — this repo's real defects lived
  there: the empty input, the concurrent second actor, the interrupted
  operation, the malformed file, the caller with the wrong identity. A
  feature with only happy paths is half a contract.
- Refusals are behavior. This codebase treats a recorded denial as success
  (exit 0) and distinguishes refusal from integrity — scenarios must too.

## Traceability, both directions

- Each test that implements a scenario carries the scenario name — in Rust,
  in the test's doc comment; in Python, in the docstring. Grep must be able
  to find the scenario in the test tree, e.g.
  `grep -rn "Scenario: a second claimant is refused" crates/ .dev/`
  (`crates/` is the gate kind's forward-looking test scope; it does not exist
  until the product's first package lands).
- Before leaving red, sweep the feature file: every scenario either has a
  test naming it, or an explicit `# deferred: <why>` line in the feature.
  A scenario silently unimplemented is the gap nobody notices — the same
  class as a check silently dropped from CI.
- Deferral is an escape valve, not a loophole: the deferred list goes in the
  PR body verbatim, and the review stage judges it — a feature whose
  negative-space scenarios are all deferred is a `finding`, not a pass.

## Handoff

Scenarios done → follow `ops/process/tdd.md`. Give whoever fills the
test-author role the feature file(s) and the gate-kind spec; the scenarios
are the contract they test against.
