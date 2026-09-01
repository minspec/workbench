# ops — the MinSpec conductor lane

The working system for the all-repos-at-once phase: the maintainer
directs, a conductor session coordinates, workers are dispatched
harness CLIs, and everything binds to the lane (org `CONTRIBUTING.md`,
Maintainer-Directed Agent Work). Brought over from the maintainer's
dev lane, hand-run and interim by design — it dissolves into the
Symfony application via Flex recipes when the framework carries
coordination as services (see `AGENTS.md`).

## The instruments

| run | answers |
|---|---|
| `ops/bin/board.sh` | what is open, per repo: PRs, worktrees — measured now, never recalled |
| `ops/bin/link-ops.sh` | make `ops` reachable from every sibling clone as an untracked, locally-excluded symlink |

A dispatch wrapper (launching harness workers on worktrees from scope
files, House-style) is deliberately not authored by an agent: the
harness reserves agent-spawning infrastructure for the human. The
maintainer installs it at `ops/bin/dispatch.sh` when wanted; its
staged text lives with the conductor session. Until then, delegation
runs through peer sessions and the House dispatch lane.

## The rules the lane binds

- One line of work, one worktree (`../wt/<repo>/<slug>`); the main
  checkout belongs to nobody; branches are never deleted.
- Workers commit with `Source:` and `Co-Authored-By:` trailers naming
  the model that actually ran; the dispatch never names a model on its
  own — the maintainer supplies one, or the harness default rules and
  is recorded.
- Work arrives as draft PRs; the maintainer merges. A dispatch's scope
  file is copied beside its log, so what was asked and what happened
  are one record.
- `ops/dispatches/` is runtime evidence, gitignored; the lane's law is
  tracked, its residue is not.
