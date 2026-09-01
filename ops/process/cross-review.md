# Cross-review: the other harnesses judge the one holding the work order

Three harnesses work this repo — Claude, Codex, Grok — and any of them can
hold a work order: dispatch the work, route on what comes back, and commit.
Holding one is not a privilege of any harness, and **whoever holds it does
not review its own work: the others do.** Independent review is the only
thing that has reliably caught what a producer's own green suite missed,
whichever harness was producing.

That role carries no name here on purpose. `Conductor` is the prod-lane
mini-app that drives the loop; in `ops/devlane/task/` and `.dev/guide/` it
means that program, and reusing it for a dev-lane session would put one
word on two objects across two lanes.

**This file covers review only.** Who *produces* each artifact, in what
order, and what each producer is denied is `ops/process/pipeline.md` —
scope, plan, tests, code, and the rule that no producer owns two
consecutive artifacts. Reading this file alone gives three roles; the
sequence has six stages.

## Who reviews what

A constraint, not a roster: **no harness reviews an artifact it produced,
and no harness owns two consecutive artifacts.** `pipeline.md` fixes the
sequence; this says what review may not be.

| artifact | produced by | reviewed by |
|:--|:--|:--|
| plan | Fable | Codex |
| tests | Grok | Codex |
| code | Opus | Grok **and** Codex |

Filling two roles is allowed, and with six stages and four producers it is
unavoidable — Grok writes the tests and reviews the code, Codex checks the
tests and reviews the code. Neither reviews what it wrote, and neither
pair is consecutive. Roles are cards, not models:
`ops/process/roles/test-author.md` and `ops/process/roles/test-skeptic.md`
say what the role does; the table says only who fills it this cycle.

Two reviewers rather than one, so the rule survives whoever is driving: if
the session that set the scope also reviews, the other reviewer is still
independent of it.

**A fresh session is most of the protection.** Every reviewer is launched
cold against a detached snapshot, so a harness that produced earlier in
the sequence arrives carrying none of it — it cannot defend an artifact it
does not remember making. Context carried between roles is what turns a
second look into a rubber stamp, and there is none here.

What that does *not* remove is a shared blind spot. The model that wrote a
weak test will not see the gap when it reviews code against that test, no
matter how cold the session, because the limitation is in the model and
not in its memory. Fresh sessions defeat motive; only a different model
defeats correlated error. That is why the two code reviewers are two
different harnesses, and why stage 4 is a third.

**The residual, stated plainly:** the session that produced an artifact is
the one that decides which findings against it to act on. No fresh session
fixes that, because the adjudicating session is the producing session. The
owner is the backstop, and every finding must be answered in writing —
accepted or refuted with evidence — never silently dropped.

## The snapshot rule (non-negotiable)

Reviewers have tool access. They review a **detached snapshot**, never the
live worktree, so "review only" is enforced rather than trusted. One
snapshot per reviewer:

```sh
SNAP=$(mktemp -d)
git archive <sha> | tar -x -C "$SNAP"
git diff <base> <sha> > "$SNAP/REVIEW.diff"
git -C "$SNAP" init -q
git -C "$SNAP" add -A && git -C "$SNAP" commit -qm "snapshot of <sha>"
```

The commit matters: a reviewer that orients itself with `git rev-parse HEAD`
finds one, instead of an unborn branch it may misread as a broken checkout.
The snapshot is the enforcement — a reviewer can write only to a throwaway
copy. Sandbox flags below are defense in depth on top of it, not the
boundary itself.

## Invoking each harness as a reviewer

All of these run long (Grok has exceeded 10 minutes on a ~3.5k-line review).
Background them, capture to files, keep working:

```sh
cd "$SNAP"      # this reviewer's own snapshot
touch launched  # marker: the supervisor finds the stream this launch opens
nohup grok --prompt-file prompt.txt --output-format plain --max-turns 40 \
  > review.txt 2> review.err &
RPID=$!

# codex and claude launch the same way — prompt via stdin, never "$(cat ...)":
#   codex exec --sandbox read-only - < prompt.txt
#   claude -p --permission-mode plan < prompt.txt
```

Only two of the three can be told a ceiling of their own: `grok --max-turns
<N>` (40 here — the 2026-08-24 verify completed in 16) and `claude
--max-budget-usd <amount>`. Codex has neither, on the CLI or in its config,
so for Codex the battery below is the only wall there is.

Use the CLIs from PATH (a hard-coded home-directory path is one machine's
truth); `grok` also accepts `--sandbox <profile>` and `--permission-mode`
where configured. **When a reviewer CLI is unreachable, run the ones that
are and name the missing reviewer in the PR body** — a review that silently
shrank is the same lie as a check silently dropped from CI.

`$!` names the reviewer only when the launch is a plain background
command: `cd "$SNAP" && nohup … &` backgrounds the whole list, so `$!`
names a wrapper shell and a later `--terminate` kills the wrapper while
the reviewer runs on (verified with `ps -o comm=` on both forms). Feed
the prompt by file or stdin — `"$(cat prompt.txt)"` re-parses the
prompt's backticks and `$()` through the shell before the reviewer
sees it.

### Arm the tripwire battery over every launch

Nothing above watches a backgrounded reviewer, and a hung one has cost an
hour before anyone looked. Supervise each launch with the live battery,
`ops/devlane/telemetry/breaker.py`: the launch above dropped the `launched`
marker; find the stream the reviewer opened and point the battery at it
with the reviewer's PID:

```sh
STORE=~/.grok/sessions    # store root and stream pattern for this
PATTERN=updates.jsonl     # reviewer's harness — see the table below
until STREAM=$(find "$STORE" -name "$PATTERN" -newer launched \
        -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-); \
      [ -n "$STREAM" ] || ! kill -0 "$RPID"; do sleep 2; done
[ -n "$STREAM" ] && python3 ops/devlane/telemetry/breaker.py "$STREAM" --pid "$RPID" \
  --cap 2000000 --cap-out 150000 --stall 600 --size-mb 50 --terminate \
  --tripped-file TRIPPED.md 2>> breaker.log &
```

`-newer` alone can match a sibling session's stream when two runs of
the same harness overlap; the `sort` picks the newest by mtime, and a
truly concurrent launch should verify the stream is its own (Codex:
the rollout's session_meta cwd) before trusting the caps.

The caps must be armed explicitly: `--cap` and `--cap-out` default to 0,
and a zero cap disables that wire — the table's token rows are only true
when the recipe passes them. The `kill -0` ends the wait if the reviewer
died before opening a stream; a rotted launch line exits in two seconds,
and nothing should sit waiting for its stream after that.

`--cap` counts the harness's cumulative token total, and that is not
spend. On the verify run measured 2026-08-24, 2,047,199 total was
1,892,352 cache re-reads, 143,691 uncached input and 11,156 output,
against a context that peaked at 140,793. The wall tracks how long a
review ran, not what it cost, so a cap set from a cost figure kills a
healthy reviewer inside a few turns. `--cap-out` is the one token wire
that means what it says. Until a real-spend wire exists, leave `--cap`
high enough to catch only a runaway.

Which stream, and which wires can fire (store shapes measured 2026-08-21):

| reviewer | store root / pattern for the stream | wires |
|:--|:--|:--|
| Claude | newest `*.jsonl` under `~/.claude/projects/<slug>/`, where `<slug>` is `$SNAP` with every `/` and `.` replaced by `-` | all six |
| Codex | newest `~/.codex/sessions/*/*/*/rollout-*.jsonl` | tokens, tokens-out, stall, size |
| Grok | `updates.jsonl` in the newest session dir under `~/.grok/sessions/<url-encoded $SNAP>/` | tokens, tokens-out, stall, size |

Grok records its spend (updates.jsonl `turn_completed` events, measured
2026-08-21) and the battery parses it — cumulative per run, runs split
when a reported total shrinks — so grok streams feed the token walls
(`--cap`, `--cap-out`) like the others. Only repeat-loop and
error-storm stay claude-only: grok streams carry no tool_use or
tool_result records to feed them.

A trip is exit code 3: the battery prints its evidence to stderr, writes
the TRIPPED file named by `--tripped-file` into the snapshot, and with
`--terminate` it kills the runaway reviewer. A killed review did not
finish: name the tripped reviewer in the PR body exactly as you would an
unreachable one — a review cut short silently is the same lie as one that
silently shrank. Tune the wire's flag, or `--disable` the wire, only when
the tripped pattern turns out to be legitimate work.

The prompt names the tip SHA, points at `REVIEW.diff`, says "review only —
do not edit", lists the specific decisions to aim skepticism at, and demands
the wire format from CONTRIB.md §Review protocol (VERDICT / STAMP /
FINDINGS with P1–P3) so stamps aggregate across harnesses.

It also says how to land: **if you judge you are running long, stop and
write up what you have.** Every ceiling above kills the process and leaves
an empty `review.txt` — the 2026-08-24 Codex kill cost 2,047,199 tokens and
returned nothing, where a partial review would have been worth reading.
This is an exit instruction, not a budget. Never give a reviewer a token or
dollar figure: it has no counter to read, so the number cannot be obeyed,
only performed — and a reviewer that believes it is short of budget skims,
which is the opposite of why it gets a whole snapshot.

The GitHub Codex bot is separate from local `codex exec`: **only the owner
triggers `@codex review` on a pull request** — no harness posts that trigger.

## Acting on findings

**A review that returns CHANGES is adjudicated. That is not a judgement
call.**

The producer must not be the one deciding which findings against its own
work are worth acting on — that is the last dial left in the hands of the
party with the conflict, and no fresh session removes it, because the
session deciding is the session that produced. So: any review returning
CHANGES goes to a harness that produced **neither the artifact nor the
finding**, before any of it is worked.

| findings against | producer | finder | adjudicator |
|:--|:--|:--|:--|
| plan | Fable | Codex | Grok, or the session |
| tests | Grok | Codex | Fable, or the session |
| code | Opus | Grok and Codex | **Fable — the only one left** |

The adjudicator is given the artifact at the SHA that was reviewed and
the reports verbatim, and nothing else: not who wrote the branch, not
which findings the producer concedes, not what has been changed since.
It rules UPHELD / PARTIAL / REJECTED per finding, and is asked what the
reviewers missed — the 2026-08-25 run returned two such items, one of
them a hole the producer's own fix had opened.

Rule on it before working it. A producer that fixes what it agrees with
first has already adjudicated, whatever it does afterwards.

**This is prose, and prose runs nothing.** Making it a gate needs review
verdicts recorded as artifacts in the tree rather than left in a
scratchpad, so a check can refuse a merge that carries a CHANGES verdict
with no ruling against it. Until that exists the rule is honoured by
hand, which is exactly the condition under which the last one was
written down and ignored.

- **Verify every finding against the live tree before applying it.** Not
  because reviewers are often wrong — they have not been — but because the
  verification is two commands and produces the reproduction that belongs in
  the fix commit.
- The fix commit uses CONTRIB.md's template: `Finding:`/`Verified:` pairs,
  `Co-Authored-By:` the finder, `Reviewed-by:` the reviewer. The reviewer
  never pushes; the session holding the work order commits.
- A review is pinned to the SHA it read. Any push voids it; re-run against a
  fresh snapshot rather than reporting an old pass as current.
- Record the literal stamp the reviewer printed as evidence, but write
  trailers in the owner's prescribed form (e.g. Grok has stamped itself
  `Grok 4`; trailers say `Grok 4.6 <noreply@x.ai>`).
