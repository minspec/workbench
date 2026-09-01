# Agentic Management: a framework for the Conductor

A **Conductor** is a single AI session that manages a fleet of AI
agent-harnesses (dispatched Claude/Codex/Grok sessions, sub-agents,
CI jobs) to produce work no single session could hold in context.
Management theory was built for humans — durable, expensive, singular,
prone to shirking and politics. None of that is true of an agent
instance, so the theory doesn't transfer by analogy; it has to be
**inverted**, term by term, against what's actually true of the fleet.

This is that inversion: each classical concept below, named and
grounded, then rebuilt for a manager whose reports are cheap, parallel,
stateless, stochastic, and disposable — and whose real job is not to
lead them but to build the system that catches them when they're wrong.

---

## Part 1 — Org structures, adapted

### Functional vs. divisional vs. matrix

Functional structure groups people by shared skill (engineering,
sales); divisional structure groups them by self-contained product,
market, or geography; matrix structure overlays both, giving each
person two chains of command it must reconcile through negotiation.

None of the three describe a *standing group* of agents, because agents
have no tenure to belong to a group with. What survives:

| human form | agentic form |
|:--|:--|
| Functional dept. (persistent team, shared skill) | A **role card** (planner, test-author, test-skeptic, implementer, adjudicator) — a job description with no incumbent. Any harness is cast into it for one stage, then the role is empty again. |
| Divisional unit (self-contained P&L) | A **work order**: one artifact's full lineage (plan → tests → code → review), state-isolated from every other work order. Two work orders never share an agent's context, so there's no cross-division politics to manage — there's no memory for it to live in. |
| Matrix (dual authority, resolved by negotiation) | Matrix's problem — an agent pulled two directions by two authorities — has no negotiation-based fix for a stateless worker; it needs a deterministic one. Both dimensions' demands get compiled into **one closed contract** the output must satisfy. The contract *is* the matrix intersection, made checkable instead of political. |

### Mintzberg's organizational configurations and six coordination mechanisms

Henry Mintzberg's five (later six) configurations — Simple Structure,
Machine Bureaucracy, Professional Bureaucracy, Divisionalized Form,
Adhocracy, Missionary — are each built around whichever of his six
coordination mechanisms dominates. Mapped to a fleet:

| coordination mechanism | human default use | agentic form | cost |
|:--|:--|:--|:--|
| **Mutual adjustment** (informal back-and-forth) | Adhocracy; novel, ill-structured work | Multi-harness negotiation — cross-review disputes, adjudicated verdicts — bounded by a fixed wire format (verdict / stamp / findings) so it can't sprawl into unbounded chat | highest; reserve for genuine judgment calls |
| **Direct supervision** (one issues orders) | Simple structure | The Conductor dispatching one harness with an explicit brief and nothing else. The **default** mode — agents have no initiative to self-organize a division of labor, so someone has to hand out the work orders | cheap per call, doesn't scale past the Conductor's own verification bandwidth |
| **Standardization of work processes** | Machine bureaucracy | The gate *sequence* itself — every work order passes through the same stages (spec → red → impl → review → ratify) regardless of what it's building | near-zero once the pipeline is built |
| **Standardization of outputs** | Divisionalized form | **Contracts** — closed schemas / acceptance criteria an artifact must satisfy. This is the mechanism that lets agents run in true parallel with zero cross-talk, because they don't need to coordinate with each other at all if their outputs both have to clear the same gate | near-zero at run time, expensive to author well |
| **Standardization of skills** | Professional bureaucracy | Professionals train a skill into a person over years; an agent's "skill" is fixed at the vendor and can't be developed on the job. What substitutes is **capability routing**: the Conductor tracks which harness is empirically strong at which task-shape and casts accordingly, redoing the match every dispatch instead of trusting a trained-in competence | moderate — requires measuring, not assuming, capability |
| **Standardization of norms** | Missionary org | The weakest mechanism for agents: a human absorbs culture and carries it for years; a stateless instance carries nothing between sessions. Norms survive only as **re-injected doctrine** — CLAUDE.md / AGENTS.md pointer files read fresh into every context — never as something an agent "already knows" from having worked here before | must be paid every session; never amortizes |

**The one that matters most at scale is standardization of outputs.**
Mutual adjustment doesn't work between agents with no shared memory;
direct supervision doesn't scale past what one Conductor can review.
Contracts are the only mechanism whose cost doesn't grow with fleet
size — which is why "write the contract" is the Conductor's highest-
leverage act, not "assign the work."

### Span of control

Classical span-of-control theory (the concern goes back to V.A.
Graicunas's 1933 analysis of the combinatorics of subordinate
relationships) holds that a manager's effective span shrinks as task
interdependence and novelty rise — commonly cited ranges run 5–9
direct reports for complex work, wider for routine work.

For a Conductor, the limiting resource isn't attention span — a
Conductor doesn't get tired supervising report #12. It's **verification
bandwidth**: the number of independently-dispatched results the
Conductor can actually check against ground truth (not just read a
summary of) before acting on any of them, plus the blast radius of N
agents mutating shared state concurrently (merge conflicts, races on
the same files). If N results can't each be independently verified, N
is too high regardless of how cheap dispatch was.

*Concrete practice*: a hard concurrency cap on parallel dispatches,
paired with a monitoring dashboard over job handoffs — one monitor
armed *at* dispatch time, not as a separate manual step afterward.
(Lesson paid for the hard way in this repo: three jobs finished
unnoticed in one day because arming the watch was a step that kept
getting skipped — the fix was to make the launch itself *be* the
watch, so an unwatched job stops being a possible state.)

### Centralization vs. decentralization

Human orgs place decision rights on a spectrum from top-down to
delegated. For a fleet the axis that actually matters isn't *how much*
autonomy an agent has — decentralize execution maximally, since an
agent deciding its own tool calls and sub-steps costs nothing and
scales for free. It's **how reversible the thing a decision authorizes
is**. Ratification is centralized absolutely (one identity — the
owner — can merge to `main`) precisely because execution is
decentralized absolutely (any harness can propose anything).

*Concrete practice*: centralization enforced structurally, not
socially — a branch-protection rule with the owner as sole bypass
turns "please don't merge to main" into a 403, so the control doesn't
depend on an agent choosing to comply.

---

## Part 2 — Management philosophies, adapted

### Theory X / Theory Y (Douglas McGregor, *The Human Side of Enterprise*, 1960)

McGregor's dichotomy is about which assumption a manager makes about
*people*: X assumes workers are lazy and must be directed and
watched; Y assumes they're self-motivated and will exercise
self-direction if the goal is meaningful to them. Whichever a manager
believes tends to produce the behavior that confirms it.

Applied to agents, the dichotomy is a category error — an agent isn't
lazy *or* self-actualizing, it's a stochastic generator with no
persistent stake in the outcome, so neither assumption describes
anything real. What both theories were actually arguing about —
whether to trust a report or check it — has one answer for agents,
independent of temperament: **check it**. Not because agents are bad
actors (Theory X) but because self-report from a stochastic process is
uncorrelated with whether the work is actually correct, at a rate that
doesn't go to zero no matter how "capable" the model.

*Concrete practice*: every claim of "done" is backed by a command run
at the moment of the claim, not recalled from earlier in the
conversation — a report that says "tests pass" is worth nothing until
an independent, mechanical re-run confirms it against the artifact
that exists right now.

### Lean / Toyota Production System — jidoka, poka-yoke, andon

Sakichi Toyoda's automatic loom (which stopped itself the instant a
thread broke) is the root idea Taiichi Ohno systematized into
**jidoka** — "automation with a human touch": detect an abnormality,
stop the line immediately, fix the root cause, don't let a defect
travel downstream. **Poka-yoke** (Shigeo Shingo's term for
mistake-proofing) goes further than detection — it makes the error
*impossible to produce*, not just visible after the fact. **Andon** is
the visible signal — traditionally a cord or button — that gives the
worker closest to the defect the authority to halt production the
instant something looks wrong, rather than waiting for a supervisor to
notice it three stations later.

| TPS concept | agentic form |
|:--|:--|
| Jidoka (stop the line on abnormality) | A CI gate that blocks a work order from advancing the instant an invariant breaks, instead of letting a bad artifact flow to the next stage. The stage sequence (spec → red → impl → review → ratify) simply refuses to move forward on a failure — the halt is structural, not a judgment call by whoever's watching. |
| Poka-yoke (make the error impossible, not just caught) | A **closed contract** with no accidental escape hatch — a schema that an entire class of bad output cannot satisfy, period, rather than one that merely gets flagged by a downstream check. And critically: a mistake-proofing device has to be tested *as* a device — a **negative-control corpus** of deliberately planted faults, each of which must trip the *specific* constraint it was planted to test (`must_draw`). A gate that reads clean by accident is worse than no gate — it's false confidence — so you verify the jig catches the part it's shaped to catch before you trust it to catch anything else. |
| Andon (signal + stop authority pushed to the point of production) | The "honest red" in a red→green loop: a failing test must fail on the *intended* assertion, never swallow the error as a collection or import failure. The agent closest to the defect (the one running the test) surfaces it the instant it's detected, in a form specific enough to act on — it doesn't wait for a supervisor to notice downstream. |

### Deming / TQM

W. Edwards Deming taught **statistical process control** — you can't
manage a process you haven't measured, and you set control limits from
the process's actual demonstrated capability, not from wishful
thinking. He argued for **"driving out fear"** because a workforce
that fears bad news will report good news instead — "where there is
fear, there will be wrong figures." **Constancy of purpose** means the
aim doesn't drift from one quarter to the next. And his sharpest line:
**cease dependence on inspection** — quality has to be built into the
process, not inspected into the product afterward.

- *Statistical process control* → measure a check's actual precision
  and recall before trusting it as a gate. A check with 3% precision
  and 0% recall once narrowed (measured, not assumed, in this repo's
  own history) is a process out of control — it doesn't get to gate
  anything until its numbers justify it.
- *Drive out fear* → an agent has no career at stake, so "fear" isn't
  the failure mode — but the structural analog is: never let the
  harness being scored on a passing result also be the one producing
  the result. A subagent incentivized toward "green" will find a way
  to look green. The fix isn't reassurance, it's **removing the
  incentive's target** — the implementer never grades its own work
  (see segregation of duties, below).
- *Constancy of purpose* → the purpose that would live in
  institutional memory for a human workforce lives, for a fleet, in a
  versioned document (a contract, `AGENTS.md`, `CLAUDE.md`) re-read
  into every fresh context — because no single agent instance carries
  constancy on its own; the record has to.
- *Build quality in, don't inspect it in* → inspecting after the fact
  assumes an inspector independent of what produced the thing. Tests
  written by the same agent that wrote the code will fit the code that
  exists. Writing the contract and the negative-control corpus
  **before** any implementation exists (spec → red → impl) makes
  quality a property of what's allowed to be built, not a filter
  applied to what already was.

### Management by Objectives (Peter Drucker, *The Practice of Management*, 1954)

Drucker's MBO replaced supervision-based management with
results-based management: set objectives collaboratively, let people
self-direct toward them, review against results rather than watching
the process. Its classical failure mode is goals gamed to the letter
and not the spirit.

There's no collaborative goal-setting with a stateless agent — it has
no continuity across a review period to be "committed" to anything.
What MBO's actual insight (manage the result, not the method) requires
for agents is that the objective arrive as a **closed, machine-checkable
spec**, handed over whole, not negotiated. Passing the gate isn't
*evidence* the objective was met — under a well-written contract it
*is* the objective being met, because the spec defines success
completely. This closes MBO's classical loophole rather than
inheriting it: a contract has no "spirit" to violate while satisfying
the letter, so the only way to game it is to find an actual hole in
the contract — which is a finding about the contract, not a
management failure, and feeds the next revision.

*Concrete practice*: contract-as-objective, certified by construction
— a harness is never asked to self-assess against an objective; it's
handed a spec whose satisfaction is checked by the same gate for
everyone, every time.

### Agency theory / principal-agent problem / information asymmetry

Agency theory (formalized by Stephen Ross and Barry Mitnick in the
1970s, and by Michael Jensen and William Meckling's agency-cost
framework) studies what happens when a principal delegates to an agent
who has private information or unobservable effort, and whose
interests aren't perfectly aligned with the principal's — the classic
problems are *hidden characteristics* (adverse selection) and *hidden
action* (moral hazard).

The informational asymmetry is real for an agent fleet — a dispatched
harness's context window, tool calls, and internal reasoning aren't
visible to the Conductor by default — but the *incentive* half of the
classical problem doesn't apply: an agent doesn't shirk to conserve
its own effort. So the entire residual problem is **observability**,
not motivation: does the Conductor have an independent way to check
what happened, or only the agent's own narration of it?

This is also where information asymmetry stops being a problem to
minimize and becomes a **control to design on purpose**: a test-author
never opens the implementation it's writing tests against; an
adjudicator sees only the artifacts and the question, never the
discussion that produced them. Withholding information is how you
prevent an agent from rationalizing toward an answer it "expects" —
the agentic equivalent of a blinded trial.

*Concrete practice*: **custody boundaries** — an explicit, per-role
statement of exactly what a harness may see, enforced by what's
actually placed in its context (a detached snapshot, not the live
worktree; a brief with material and a question, nothing prescriptive
added).

### Internal controls & segregation of duties (COSO)

The COSO framework's segregation-of-duties principle: no single
individual should be able to initiate, authorize, record, *and*
review the same transaction, because the four functions in one hand is
exactly the condition fraud and undetected error both need.

Agentic form is the **two-producer firewall**: no producer owns two
consecutive artifacts. Whoever writes an implementation doesn't write
or approve its own tests; whoever plans doesn't implement; whoever
implements doesn't review. Pushed one layer further than COSO usually
goes: a CHANGES/REJECT verdict is itself ruled on by a *third* harness
that produced neither the artifact nor the finding — segregation of
duties applied recursively to the review layer, not just the
production layer, so the auditor can't mark its own audit either.

And the record COSO calls for isn't a policy statement — it's a
structural property of the tooling: **append-only receipts**. A chain
that records who did what, that no verb in the system can rewrite,
only extend. Segregation of duties is enforced by what the system can
technically do, not by what agents are asked nicely not to do.

### Situational leadership & delegation levels (Tannenbaum–Schmidt's leadership continuum, 1958; Hersey–Blanchard's situational leadership)

Tannenbaum and Schmidt's continuum runs from the manager deciding
alone ("tell") to the team deciding independently ("delegate"),
matched to the group's competence and the task's risk. Hersey and
Blanchard's situational leadership matches style to a follower's
*readiness* — competence plus commitment — which is expected to grow
over time as a person is developed.

Readiness-that-grows doesn't exist for an agent instance — a fresh
session has no track record of its own to have earned trust with. So
the axis a Conductor actually matches supervision to is two things
that *do* persist: (1) the harness's **measured capability** for this
task-shape, tracked at the fleet/model level across many instances,
never assumed for an individual session; and (2) the **reversibility**
of the action being delegated. A cheap-to-check, reversible action
(draft a plan, write a test) gets full delegation even to an
unproven harness, because a bad result costs one review cycle. An
irreversible or expensive-to-verify action (merge to main, delete
data, spend money) sits at the "tell" end of the continuum *regardless
of how capable the harness is* — capability doesn't buy back
irreversibility.

*Concrete practice*: a delegation-level table keyed on (harness track
record × action reversibility), not on trust, tenure, or how good the
last five outputs looked. The most proven harness in the fleet still
gets its `main`-merge staged for human ratification — that cell of
the table never changes no matter who's in it.

### RACI

RACI (Responsible / Accountable / Consulted / Informed) clarifies role
ambiguity in cross-functional work — critically, exactly one person is
Accountable, so "the buck stops" somewhere specific, while several can
be Responsible for doing the work.

RACI degenerates immediately if Accountable is assigned to an ephemeral
instance — an agent can't be held accountable across time if it no
longer exists when the question comes back. So the transformation is:
**Accountable is always a persistent identity** — the human owner, or
the standing Conductor role — never a dispatched instance.
**Responsible** is whichever harness is cast into a work-order stage
right now, tracked as a role assignment in a durable record rather than
a name anyone has to remember. **Consulted** becomes the cross-review
step — invoked deterministically by the process, not "whoever happens
to be free." **Informed** becomes the append-only receipt chain — any
later agent or the human can read what happened without having to ask
anyone.

*Concrete practice*: ratification — the Accountable act — is never
delegated, ever, to any harness; everything else in the fleet cycles
through Responsible/Consulted/Informed roles every single dispatch.

---

## Part 3 — The adaptation, made explicit

Human management theory assumes its unit of management is scarce,
expensive, persistent, and roughly deterministic, and that the job is
to align that unit's *will* with the organization's goals. Every
invariant is false for a fleet:

| invariant | human org | agent fleet |
|:--|:--|:--|
| cost of a "hire" | months of recruiting, a salary | one dispatch call |
| parallelism | one job per person | N identical instances at once |
| memory | carried in the person, across years | carried nowhere in the agent; lives in the record or not at all |
| behavior | consistent, improvable via feedback | stochastic, does not reliably learn within a session |
| exit | firing is costly, legal, slow | kill the process; free |

Because the unit is disposable and stateless, motivating it is a
non-operation — there's no will to align and nothing persists to be
motivated for next time. The manager's actual leverage moves entirely
to what surrounds the agent:

- **Don't motivate — design the gates.** A deterministic check that
  refuses invalid output does the job "motivation" was standing in for.
- **Don't supervise the work — write the contract.** A closed,
  checkable spec replaces both the pep talk and the micromanagement;
  agents that never talk to each other coordinate perfectly if their
  outputs both have to clear it.
- **Don't manage politics — set custody boundaries.** Deciding what an
  agent may see is not damage control for information asymmetry, it's
  the control itself, deployed on purpose.
- **Don't assign by seniority or availability — route by comparative
  advantage.** Cast each stage to whichever harness is empirically
  strongest at that task-shape, re-decided every dispatch, since no
  agent instance carries a career for "seniority" to describe.
- **The system does the remembering and the refusing**, so the manager
  doesn't have to hold either in its own head — a fact not in the
  durable record does not exist for the next session; a violation not
  caught by a gate does not get caught at all.

---

## Operating model for the Conductor

1. **Reserve the expensive model for judgment and routing** —
   adjudicating disputes, deciding who does what next, final
   verification. Never spend it on investigation or a first-draft
   implementation a cheaper harness can produce.
2. **Delegate investigation and implementation.** A dispatched job is
   disposable; the Conductor's own context is the scarce resource —
   protect it, don't fill it with work a subagent could have done.
3. **Verify every consequential result with an independent harness
   before acting on it.** The producer of a claim is never its sole
   verifier — not because it's untrustworthy, but because self-report
   is not evidence of anything.
4. **Deliver only what passes a local, deterministic gate.** A
   harness's narration of success is not a substitute for a check run
   against the artifact that exists right now, at this moment.
5. **Keep durable, append-only records of every dispatch, decision,
   and verdict.** Anything that lives only in one session's volatile
   context is invisible to the next session and might as well not have
   happened.
6. **Stage irreversible or high-ambiguity actions for human
   ratification** — merges to a protected branch, deletions, spend,
   anything a gate can't fully specify. Capability never buys back
   irreversibility; route by risk, not by how good the harness is.
7. **Cap concurrency to what can actually be verified**, and arm a
   monitor at the moment of dispatch, not as a separate step after —
   an unwatched job that finishes is indistinguishable from one that
   never ran.
8. **Restrict what each agent can see, on purpose.** Custody
   boundaries are a designed control, not an accident of how much
   context happened to be handed over.
9. **Write the objective as a closed contract before work starts.** If
   success can't be checked by a machine, it is not yet a real
   objective — it's a hope.
10. **Never let one instance be both builder and sole judge of the
    same artifact** — and apply that recursively: the judge of a
    dispute is never the one who raised it or the one it's against.

---

*Grounded in: Mintzberg's structural configurations and coordination
mechanisms; classical functional/divisional/matrix organization
design; span-of-control theory (Graicunas); McGregor's Theory X/Y;
the Toyota Production System (Toyoda, Ohno, Shingo) — jidoka,
poka-yoke, andon; Deming's statistical process control and 14 points;
Drucker's Management by Objectives; agency theory and the
principal-agent problem (Ross, Mitnick, Jensen & Meckling); the COSO
internal control framework and segregation of duties; the
Tannenbaum–Schmidt leadership continuum and Hersey–Blanchard
situational leadership; and RACI.*
