# token-thrift.md — not spending what the work doesn't need

`worth.py waste` points at where spend concentrated; this page names
the classes that put it there and the countermeasure for each. The
discipline: every class is detectable from the stores, so a claim
that spend improved is a `worth.py` comparison between two windows,
never an impression.

| # | waste class | signal in the stores | countermeasure |
|:--|:--|:--|:--|
| 1 | **context churn** — the same context re-read turn after turn | `cache-churn`: cached reads more than 20x output | narrower reads (offset/limit), never re-read after edit, keep long transcripts compacted, split long lanes into fresh sessions |
| 2 | **unbounded tool output** — a command dumped its whole world into context | `heavy-turn` on tool-heavy sessions | bound every command (`head`, `--short`, `-c` counts); the count-first grep shape; `Read` with limits |
| 3 | **re-derivation** — state rebuilt from scratch instead of read from the stream | many short turns re-running status/log/diff | context stream + stamps: read what moved, re-take only what a crossing expired |
| 4 | **doc reloading** — agents loading whole process docs each run | repeated large cached reads across agent sessions | compact aggregates for agents (pulse, usage report lines); docs stay for humans and first reads |
| 5 | **oversized fan-out** — parallel agents where one would do | many concurrent sessions, low per-session output | size the pattern to the surface; say the agent count before launching; solo for routine work |
| 6 | **polling** — asking again instead of being told | dozens of identical cheap turns | `--watch` modes, background tasks with notifications, breaker supervision instead of manual checks |
| 7 | **runaway reviewers** — a hung or looping background run burning quietly | breaker trips; grok runs with huge `modelCalls` | always arm the battery (cross-review.md); caps are the contract, `--disable` is refused on the armed line |
| 8 | **retry storms** — the same failing operation repeated verbatim | bursts of near-identical turns | after two failures, change the approach or surface the blocker; never loop a denied call |

The rule behind all eight: **tokens buy state changes, not activity.**
A turn that moved no file, landed no commit, and decided nothing was
either a read that should have been narrower or a wait that should
have been a notification.

Review cadence: run `worth.py waste` over the last day before closing
a lane; anything ranked in the top 5 twice in a row gets a class
assigned from this table and a countermeasure applied, and the next
window's report is the test of whether it worked.
