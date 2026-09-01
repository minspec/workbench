# worth.md — costs and results, joined

Two questions the dev lane must answer from data, not memory: *was the
recent work worth what it cost?* and *where are tokens being wasted?*
Both are answered by `ops/devlane/telemetry/worth.py`, which reads the
same stores as `usage.py` and the same repo history as everything
else. Every figure it prints is produced at run time and stamped with
the window and the repo state it was measured against. No figure from
this tool is ever USD: the only native dollar wire (grok
`costUsdTicks`) has an unverified scale, and it is reported raw as
ticks or `unrecorded`, exactly as `usage.py` does.

## worth report — cost x results

    python3 ops/devlane/telemetry/worth.py report --repo PATH \
        [--since ISO] [--until ISO] [--now ISO] [--format plain|json]

The cost block: per harness (claude, codex, grok), the sessions and
messages inside the window, and in / cached / out / total token sums.
The results block, from the repo's history alone (no network): merge
commits landed on the current branch in the window with their PR
numbers parsed from the subject, non-merge commits, and the
test-definition count at each window edge with its delta.

## worth waste — where the spend concentrates

    python3 ops/devlane/telemetry/worth.py waste --repo PATH \
        [--since ISO] [--until ISO] [--now ISO] [--top N] [--format plain|json]

Ranks sessions by total tokens and names, for each: the harness, the
session id, messages (or runs), out, and cached tokens. Signals are
numbers with provenance, never verdicts: `cache-churn` carries the
cached-read and output figures whose ratio breached, and
`heavy-turn` names the single heaviest message (for codex, the
largest step between its cumulative counts) or run. A human or an
agent decides what to change; the tool only points.

## Behavior contract (tests are authored from this section alone)

1. **Time is an input.** `--now ISO` is accepted by both subcommands;
   when absent the current time is read once. The default window is
   the 24 hours ending at now; `--since`/`--until` (ISO 8601,
   inclusive lower, exclusive upper) override either edge. No test
   may depend on the wall clock: fixtures pass `--now`.
2. **Windowing is per message for claude and codex**: a usage event
   counts iff its own timestamp is inside the window. **Windowing is
   per run for grok**: a run (as split by `usage.py`'s shrink rule)
   counts iff its last report's timestamp is inside the window; runs
   are never subdivided, because grok reports cumulatively and a
   partial run has no honest per-message delta. The report says
   `runs=` for grok, not `messages=`.
3. **Cost figures reuse the usage.py accounting** — same stores, same
   session discovery, same cwd filter for `--repo`, same last-wins
   dedup for claude ids, same grok run-splitting, cost ticks, and
   incompleteness discipline. A harness whose stores are absent or
   unparseable prints `unrecorded`, never 0. `counted=N/M` and
   `(incomplete)` propagate to every output format.
4. **Results come from git only.** Merge commits on HEAD's
   first-parent line in the window; PR numbers parsed from `Merge
   pull request #N` subjects; commits with no PR number are listed as
   commits, not dropped. The test-definition count is measured from
   the two window-edge trees (`git rev-list -1 --before` at each
   edge), never from the working tree; if an edge has no commit the
   report says `unrecorded` for the delta.
5. **The join is stamped**: repo HEAD short sha, branch, window
   [since, until), and generation `--now` appear in both formats.
6. **waste ranks by window-scoped totals**: `--top N` (default 5)
   sessions ordered by total tokens inside the window, ties broken by
   session id for determinism. `cache-churn` fires for a session iff
   cached reads exceed 20x its output tokens AND output is nonzero;
   `heavy-turn` names the single largest out-token message (claude /
   codex) or run (grok) in each listed session. Signals carry the
   session id and harness so the transcript can be opened.
7. **JSON is the same truth**: `--format json` emits one object with
   `stamp`, `cost`, `results` (report) or `stamp`, `sessions`,
   `signals` (waste); every plain-format figure appears in it, and
   nothing appears in JSON that plain omits.
8. **Exit codes**: 0 on success including empty windows (an empty
   window prints zeros for results and `sessions=0` per harness); 2
   on unusable arguments (malformed ISO, until <= since, unknown
   format); stderr carries the reason.

## Reading it

- A PR that took three review rounds and a fraction of a session's
  tokens, and landed with its findings pinned, was cheap. A refuted
  finding that consumed a round is the trigger to inspect, not a
  number to hide. Marginal cost against marginal utility, per
  decision — not totals against feelings.
- `waste` output feeds the token-thrift checklist
  (`ops/process/token-thrift.md`): each signal names the transcript
  to open and the discipline that would have prevented it.
