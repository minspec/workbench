# The tdd loop, bound to wf

Evidence lives in the chain, not in your memory of having run something.
Every step below records a receipt; if `wf` refused it, the step did not
happen. Identity first (appending verbs refuse without it):

```sh
export WF_AGENT='Your Model Name <noreply@vendor>'
```

`python3 ops/devlane/workflow/wf.py next <WO>` at any point tells you the stage,
what is missing, and the exact argv that records it. Trust it over this file.

## The stages (gate kind `tdd@1`: spec → red → impl → review → ratify)

**spec** — write the behavior down before any test. If the work came through
the `bdd` skill there are scenarios already; otherwise state the promises the
tests will pin. Advance: `wf advance <WO>`.

**red** — get the failing tests written and prove them red:

1. Independent authorship is the rule, not a preference: when a second
   harness is reachable, the **test-author role**
   (`ops/process/roles/test-author.md`) is filled by a harness that is not
   the implementer (`ops/process/cross-review.md`). A subagent given only
   the contract is the fallback when no second harness is reachable, and
   using the fallback is stated in the PR. The gate does not enforce this —
   the review stage does (see below).
2. **Prove the red per file, for the right reason.** Run each new test file
   by itself and read the failure:
   - "NO TESTS RAN" / zero collected is NOT a red — a `-k` pattern matching
     nothing exits nonzero and proves nothing. That mistake shipped here.
   - An import/collection error is NOT a red. The red must be the intended
     assertion failing because the behavior does not exist yet.
3. Record it: `wf red <WO> -- <test argv>` — the argv you record is the argv
   that gets frozen; make it the one that runs the whole intended set (for
   the product suite, once it exists, per the gate
   kind's build check). `wf red` records any nonzero exit — it cannot tell an
   honest red from a collection error, which is why step 2 is on you and the
   skeptic checks it.
4. Receipts refuse against a dirty tree (D3): commit first, or accept the
   weaker hash-only receipt with `--allow-dirty` and say why.
5. `wf advance <WO>` — advancing out of red seals the frozen set (the files
   matched by the gate kind's `test_scope`, features included). After this a
   quiet edit to a frozen file makes `wf green` refuse on manifest drift; the
   honest route back is below.

**impl** — make it pass without touching the judge:

1. Implement. Do not edit files in the gate kind's `test_scope` or
   `harness_scope` — green will refuse on manifest drift if you do.
2. Before green, have the **test-skeptic role**
   (`ops/process/roles/test-skeptic.md`) judge the sealed tests — filled by
   a harness that wrote neither the tests nor the implementation. When it
   proves a test wrong or weak, the route back is recorded, not quiet:
   `wf marker <WO> open <id>` with the finding, `wf advance --to red
   --reason "<finding>" <WO>`, fix the tests, re-prove red, reseal.
3. `wf green <WO> -- <frozen argv>` — must be the frozen command, passing.
4. `wf check <WO> <name>` for each of this kind's checks (`wf status
   --checks` lists every registered kind's checks — take the rows for yours).
   Be clear what this is: a failing check records a denial and exits 0, and
   `tdd@1` requires only `receipt.green` to leave impl — **checks do not
   block advance**. They are evidence the review stage reads, and skipping
   them is visible there as absence.

**review** — this is where the process is enforced, because the gate
deliberately is not (records are evidence, never authority — AGENTS.md).
The reviewer reads the record (`wf log <WO>`, `wf agents`) and refuses with
`finding` when it shows:

- the red and the tests' commits carry the same agent as the implementation
  commits, with no stated fallback justification in the PR;
- no skeptic pass over the sealed tests, or its findings unaddressed;
- registered checks absent or denied with no explanation;
- a red whose failure was collection or compile error, not the intended
  assertion.

`wf outcome <WO> <outcome> --by <actor> --source <src>` (`open` and
`outcome` require `--source`; the other verbs do not). Review's `finding`
returns the work order to impl; **ratify** is the owner's (D10 — recording
states it, the attesting merge applies it).

## The three honesty rules that outrank speed

- **A test that never failed has proven nothing.** Red first, per file, on
  the intended assertion.
- **The implementer's green is a claim, not evidence.** Independent tests, or
  at least the skeptic's review, before `wf green`.
- **Numbers about the work come from commands run now** — test counts and
  results in any PR body or report are produced at write time and stamped
  with the SHA they were measured against (CONTRIB.md §Evidence).
