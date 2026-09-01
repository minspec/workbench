# The pipeline: who does what, and what they may not see

`cross-review.md` says who *reviews*. This says who *produces*, in what
order, and what each producer is denied. It exists because the sequence
was run successfully on 2026-08-23, was written down nowhere a later
session would look, and was reconstructed the following night from a
15MB transcript.

## The loop

| # | stage | who | is given | is DENIED |
|:--|:--|:--|:--|:--|
| 1 | scope | this session, with the owner | the problem | — |
| 2 | plan | Claude | the scope | the freedom to move its boundaries |
| 3 | tests | Grok | the plan | the implementation (it does not exist yet) |
| 4 | check the tests | Codex | the tests and the plan | — |
| 5 | code | Claude | the tests | — |
| 6 | review the code | Grok **and** Codex | the PR | — |

Harnesses, not models. A CLI's configured model changes under it — the
Codex that reviewed on 2026-08-25 was `gpt-5.6-sol` at `xhigh` while
stamping itself `GPT-5 Codex`. Dispatch by harness; read the model from
the run and record it as evidence. The owner names the model that fills
each role (`AGENTS.md` §The owner's standing rules): plan is always
Fable; the other fills are this cycle's.

**The scope is the owner's, and that is what breaks the chain.** Rows 1
and 2 would otherwise be one harness — a Claude session writing the
scope, Fable writing the plan — which is the violation this table
exists to forbid. The scope is not the session's artifact: it is the
owner's, settled in session, and the session assists. Where the owner
is not the author of a scope, stage 1 goes to a harness that does not
hold stage 2.

**The scope is not the plan.** Scope is settled in session with the
owner — *what*, and the boundaries; the planner states *how*. It carries
no role name on purpose: `Conductor` is a prod-lane mini-app, and one
word for two objects across two lanes is a collision waiting to be made. Yesterday's scope document said it
in one line — "This file is the scope only. The plan is not mine to
write." Collapsing the two puts one author on both, which is the failure
the whole arrangement exists to prevent.

**Tests come before code, and a different harness writes each.** Of 14
review findings against two checkers built the other way round on
2026-08-24, **11 were missing test cases, not coding errors** — the
implementation did what the tests specified and the tests were
incomplete. The scarce skill is adversarial coverage, so stage 4 asks
"what input passes these tests and is still wrong?", not "are these
tests right?".

**No producer owns two consecutive artifacts.** That is the property to
preserve if the assignment ever changes; the specific model names matter
less than that constraint. Two harnesses review rather than one so the
rule survives whoever is driving: if the session that set the scope is
also a reviewer, the other reviewer is still independent.

The residual this arrangement carries: **both reviewers have a stake in
the tests** — Grok wrote them, Codex approved them — so code that
satisfies weak tests looks right to both. Stage 4 is the only thing
standing between weak tests and a clean review. Put the tests in scope
at stage 6 when the change is large.

## A plan is discharged, not archived

A plan is transient by nature and becomes permanent by accident: it
accumulates the durable content that has nowhere else to go, something
cites it, and then it cannot be retired.

`ops/devlane/workflow/PLAN.md` is the worked example. It holds four
genres — the design (§§1–10), the phasing (§11), the ratified decisions
(§12) and the Slice 1 brief (§13). Only the last two are a plan. The
decisions were dated 2026-08-20, `.dev/docs/DECISIONS.md` was created
2026-08-21, and they were never migrated. The design had no home,
because the workflow app has no contract document — so the CUE
contracts were extracted from the plan, and 1,236 citations now point
at it.

**Contracts are extracted from an app's contract document, never from a
plan.** That single rule is what keeps a plan disposable.

When the work is done, discharge it:

1. decisions → `.dev/docs/DECISIONS.md`
2. durable design → the app's `CONTRACT.md`
3. lessons → `.dev/docs/mini-app-lessons.md`
4. what remains — work items, ordering, slices — is spent; say so
5. after that nothing may cite it

A plan that survives step 5 was never a plan.

## The firewall must be proved, not intended

Withholding is invisible: a snapshot that leaked the wrong file looks
exactly like one that did not. Staging four roles by hand and checking
afterwards with `find` produced one role firewalled on one side where
the plan specified both.

Prove it in **both** directions before dispatching:

- nothing matching a withheld pattern is present, **and**
- something matching every given pattern is present.

An empty snapshot satisfies the first perfectly. A role staged from a
mistyped path leaks nothing, contains nothing, and then answers
questions about an empty directory.

Build the snapshot from a manifest and refuse to dispatch one you cannot
prove. `ops/devlane/harness/` exists to do this; a hand-staged snapshot is
the thing it replaced.

## The variant used for contract work

When the deliverable is a CUE contract rather than code, the two
producers are split differently and meet at a file neither owns:

- **contract author** reads the specification and *nothing else* — no
  modules, no SQL, no JSON, no database.
- **extractor author** reads the code with the specification *removed
  from its snapshot*, and writes mechanical extractors reporting what is
  actually there.
- both implement against a neutral shape spec written before either
  starts, which neither owns.
- `cue vet` compares. An adjudicator sees everything and rules.

Neither author can reconcile a disagreement quietly, which is the point
and is not achievable with one author however careful: whoever holds
both halves resolves a discrepancy in passing and never mentions it.

**Measured**: twelve contracts against twelve observations produced
**38 disagreements**, four confirmed against the tree by hand — among
them `wf.py` declaring 17 CLI verbs where the specification documents
16.

## The evidence behind all of this

Three documents on the shelf, `.dev/docs/`:

- `.dev/docs/cue-sys.md` — what CUE turned out to be for here, and the
  incident behind each lesson.
- `.dev/docs/cue-aar-equality.md` — four CUE constructs that read as
  constraints and enforced nothing, each of which passed its negative
  test while doing so.
- `.dev/docs/mini-app-lessons.md` — running record of what building the
  dev-lane mini apps taught, kept so the prod-lane versions can be
  written better. Add to it while the measurement is still on screen.

Read them before writing a contract. They are the record; this file is
only the sequence.
