# The task app — a calling convention for delegated work

A **task** is one delegated unit of work that returns one deliverable
in one round. It is not a conversation and not a workflow stage: the
Conductor invokes it with parameters, the task does its own iteration
internally, and it returns a typed envelope.

The point is not correctness — the checks and the cross-review process
already own that. The point is a **standard, token-efficient way to
run delegated work**, so the plumbing is written once and the Conductor
never parses prose.

## Why it exists, measured

On 2026-08-22 one session launched **36 agents** and hand-wrote the
same ~40 lines of plumbing for nearly every one: snapshot a ref,
archive it, write a diff, drop a marker, discover the session stream
the harness just opened, arm the battery, wait, read a verdict by eye.
**15 of those 36 were repeat rounds of a review already run**, each
handed the entire diff again even when the live question had narrowed
to a single test case — because there was no parameter for scope.

Two costs follow from that, and both are what this app removes:

- **Plumbing retyped per launch**, with the mistakes that come with it.
- **The Conductor reading work product.** Verdicts arrived as prose, were
  read in full, and the interesting parts were retyped into the next
  prompt. That is the loop cost: a Conductor's context grows with every
  round until it cannot run another.

## The call

```
task(
    job,                                   # which pre-made task
    context = {ref, diff_base, include, prior},
    require = {schema, scope, constraints},
    runtime = {harness, model, effort, caps},
)
```

**`context` is the token lever.** It states what the task may see:

| field | meaning |
|:--|:--|
| `ref` | the git ref to snapshot — the task works on a detached copy, never the live tree |
| `diff_base` | what the diff is taken against; omitted means no diff is written |
| `include` | explicit path allowlist. Absent means the whole snapshot, which is the expensive default and should be a deliberate choice |
| `prior` | findings from an earlier call. Turns "review this again" into "confirm these are closed", which is a much smaller job |

**`require`** states what must come back: the output `schema`, the
`scope` to aim at, and the `constraints` the task must respect (read
only, plant on copies, never edit the snapshot).

**`runtime`** states who runs it and under what ceiling: `harness`,
`model`, `effort`, and the supervision `caps` handed to
`ops/devlane/telemetry/breaker.py`. A job never names a model — that
is the Conductor's dial, which is what makes "eight cheap ones" and "one
careful one" the same job.

## The envelope

Every task returns the same shape. The Conductor routes on it without
reading the work:

```json
{
  "job": "adversarial-review",
  "status": "ok | invalid | tripped",
  "verdict": "approve | changes | null",
  "counts": {"p1": 0, "p2": 2, "p3": 1},
  "findings": [
    {"severity": "p2", "where": "file.py §section", "claim": "...",
     "reproduce": "python3 -m unittest ..."}
  ],
  "artifacts": {"raw": "<path>", "diff": "<path>"},
  "spend": {"harness": "grok", "total": 0, "out": 0, "runs": 0},
  "stamp": {"ref": "<sha>", "started": "...Z", "ended": "...Z"}
}
```

Three fields carry the weight:

- **`reproduce`** — the command that re-derives the claim. A finding
  without one is an opinion, and the envelope says so rather than
  hiding it.
- **`artifacts`** — handles, not contents. The prose stays on disk;
  the Conductor reads it only if it decides to.
- **`spend`** — what the run cost, so `worth.py` can price a job
  instead of guessing. Runs happen in throwaway snapshots today, and
  usage.py's cwd filter therefore attributes **none** of them to the
  repo they served: roughly 118M tokens of review on 2026-08-22 went
  unattributed. The stamp is what closes that.

## Statuses, and the refusal rule

`status` is the routing decision, and it is not the same as `verdict`:

| status | means |
|:--|:--|
| `ok` | the task ran and produced its deliverable |
| `invalid` | the task could not do its job — harness missing, ref absent, snapshot failed, output unparseable. **Never reported as an approving verdict.** |
| `tripped` | the battery stopped a runaway; the deliverable is partial and says so |

A task that could not look must not return `approve`. This is the same
rule the registered checks follow, and the reason `status` and
`verdict` are separate fields rather than one.

## Jobs

A job is data, not code — a JSON entry naming the role, the prompt
template, the output schema, and the default constraints. Adding one
does not change the runner.

| job | deliverable |
|:--|:--|
| `adversarial-review` | findings that survived the task's own attempt to refute them |
| `verify` | one claim, executed: a reproduction or a refutation |
| `author-tests` | a test file that has been run and is red for the stated reason |
| `sweep` | structured findings over disjoint categories, merged |
| `plan` | a plan written to the job's `out/PLAN.md`, and an envelope naming it |
| `check-tests` | a skeptic's report at `out/SKEPTIC.md`; verdict `changes` when any test is refuted |
| `implement` | commits in the snapshot that make the named test command pass, trailers per `AGENTS.md` |
| `adjudicate` | a ruling at `out/RULING.md` — UPHELD / PARTIAL / REJECTED per finding, and what the reviewers missed |

`verify` is deliberately callable on its own: it is the primitive the
others lean on, and the one whose answer is mechanical — an exit code
or a diff, not another opinion.

The last four are the dev-lane pipeline's own stages as jobs (dispatched
by the dispatch app; see `ops/devlane/dispatch/CONTRACT.md`). They are data
in `jobs.json` today. Three of them — `plan`,
`check-tests`, `adjudicate` — name `{out}` or `{inputs}`, which `run.py`
does not yet supply, so `render` refuses each by name rather than
launching a brief with a hole in it. `implement` names only `{ref}` and
`{scope}` and renders today; what it still lacks is the launcher's
whole-mode snapshot and collect, without which its commits have nowhere
to land.

## What the runner does, and does not

The runner wires: it snapshots, launches, supervises with the battery,
collects, parses, records. It does **not** read the work product, and
it does not decide anything a job could not have decided in
advance.

Two things stay outside it. **Intent** — which of two defensible
designs is right — belongs to the job author or escalates to the
owner; a worker task has no standing to settle it. And **the wiring's
own correctness** cannot be delegated to the tasks it wires, which is
why every job is bound to a planted fault: run it against a fixture
with a known defect and it must surface it; run it clean and it must
stay quiet. A job that cannot catch its own planted fault is a
no-op with good throughput.

## Harnesses

The job is harness-independent; an adapter absorbs the differences
in invocation, in where each harness writes its session stream, and in
how each reports usage (`ops/devlane/telemetry/usage.py` already holds
those three accountings). A `stub` adapter exists so the suite can
test the runner without spending a token or needing a model.

## The property to hold

**Iteration N should cost the Conductor about what iteration 1 cost.**
A loop whose context grows every round has a horizon; one that stays
flat can run as long as the work does. The envelope is what makes that
possible, and `worth waste`'s cache-churn signal is how it is checked.

## Dispatch — moved to its own app

The dev-lane launcher — `launch.py`, the policy over `run.py`, and
its record builder `record.py` — no longer lives here. It executes
the harness app's isolation and reads its budget wiring, so it
genuinely depends on `harness`; the cross-app import contract forbids
`task → harness`. Rather than weaken that rule, the launcher moved to
a composition app that may depend on both this app and `harness`:

- its contract is `ops/devlane/dispatch/CONTRACT.md`;
- its guide page is `.dev/guide/dispatch.md`;
- the allowed edges `dispatch → task` and `dispatch → harness` are
  declared in `.dev/contracts/imports.json`.

`jobs.json` stays here — it is data the dispatch app reads by path,
not an import — and so do `run.py`, `envelope.py`, `fileset.py`, and
`verify.py`.
