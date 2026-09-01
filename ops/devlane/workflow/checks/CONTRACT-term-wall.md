# The term wall — contract v1

Names this organisation does not use must not appear in any of its
repositories: not affirmed, not negated, not cited. The wall refuses
them, and the wall itself never carries them in any readable or
encoded form.

## Instruments

1. `.github/actions/term-wall/term-wall.sh` (run by the composite
   action `.github/actions/term-wall`) — every repo's `ci` job runs it.
2. `ops/devlane/workflow/checks/term_wall.py` — the lane's local copy,
   run by the commit-msg hook and by apply-push's guards.

## The pattern is configuration, never tree content

- The pattern is an extended, case-insensitive regular expression read
  from the environment variable `TERM_WALL`. In CI the action takes it
  from the org-level Actions variable `vars.TERM_WALL`. Locally the
  Python check reads `TERM_WALL`, falling back to the gitignored file
  `<root>/ops/bin/term-wall.conf` (one line: the pattern).
- No tracked file may contain the pattern, a piece of it, or any
  encoding of the names (hex, base64, bracket tricks, escapes). The
  self-test's planted fault comes from `vars.TERM_WALL_PLANT` (a string
  the pattern matches), never from the tree.
- An unset or empty pattern is a refusal, exit 2, stdout empty, one
  line on stderr: `pattern: expected TERM_WALL set; found empty;
  needed the org variable (CI) or ops/bin/term-wall.conf (local)`.
  The wall never passes vacuously.

## Surfaces (term-wall.sh)

1. tracked content — every `git ls-files` path, binaries skipped,
   case-insensitive;
2. tracked paths;
3. the commit messages of the change — `pull_request`: `base..head`;
   `push`: `before..head`, or only the head commit when `before` is
   all zeros — read from git (fetching what the checkout lacks), never
   from an API: the action needs no token and declares none;
4. the pull request title and body (from the event payload);
5. the branch name (`GITHUB_HEAD_REF` for a PR, `GITHUB_REF_NAME` for
   a push).

Outside GitHub Actions (no `GITHUB_EVENT_PATH`), surfaces 1 and 2 run
against the current directory. The Python check covers surface 1 and
2 (`[--root DIR] [PATH ...]`), one message (`--message-file FILE`), a
range of commit messages (`--range BASE..HEAD`), or `--stdin`.

## Outcomes, on the wire

| exit | meaning |
|---|---|
| 0 | clean; one summary line on stdout |
| 1 | at least one hit; every hit printed on stdout as `<surface>: <location>: <line with each match replaced by [forbidden name]>`; a surface that could not be read (fetch failed, payload unreadable) is itself a hit — could-not-look is never a pass |
| 2 | refusal: pattern unset/empty, not a git work tree, missing message file, unresolvable range; stdout empty, one stderr line `class: expected …; found …; needed …` |

The raw matched text never appears in any output.

## Self-test (the `.github` repo's own ci)

With `TERM_WALL_PLANT` as the planted fault: a planted file fires
(exit 1, masked hit), a clean neighbour stays quiet (exit 0), and an
empty `TERM_WALL` refuses (exit 2).

## Tests

Tests execute the real instrument as a subprocess inside temporary git
repositories they create; nothing about the wall is mocked. They set
`TERM_WALL` explicitly to a test-only pattern (for example
`zz[q]orblat`) and plant matches of it, so no forbidden name exists
anywhere. They are deterministic and hermetic: no network, no sleeps,
no dependence on the caller's cwd, environment, or git identity
(configure user.name/user.email in each temp repo). Every test asserts
the exit code and the output shape. Push and pull-request events are
simulated with an event JSON file and the `GITHUB_*` variables.
