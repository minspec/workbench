# AGENTS.md

This repository is `minspec/workbench`.

## Repository role

`minspec/workbench` is the maintainer-only, Mate-enabled host application for MinSpec: a real Symfony application where MinSpec packages and recipes are validated — installed, wired, and exercised — and the primary working environment for maintainer-directed development. Unlike `minspec/skeleton`, this repository holds a real application: `composer.lock` is committed, configuration is committed, and Mate lives here as a dev dependency.

## Operating mode: maintainer-directed agent work

Agents work here under the maintainer-directed lane defined in the org `CONTRIBUTING.md` (Maintainer-Directed Agent Work): maintainer direction and scope, granted accounts, draft-first pull requests, `Source:` and `Co-Authored-By:` trailers on every commit. Merging, releasing, and repository settings remain the maintainer's. Agents never rewrite history, never force-push, and never delete branches — branches are part of the record.

## Worktrees: one line of work, one working tree

This discipline — and the lane above — is the coordination system brought over from the maintainer's dev lane, run by hand and interim by design: it holds until it is dogfooded into the Symfony application itself — as packages carrying coordination as services, composed into this host via Flex recipes through MinSpec's recipes endpoint, per the package-first doctrine. When that happens, this file is where the handover is recorded.

This repository is worked by several actors at once. A `git checkout` in a shared tree yanks HEAD from under everyone else, so:

- **One line of work, one worktree.** Before starting on a branch: `git worktree add ../wt/workbench/<branch-slug> -b <branch> origin/main`. Work there. Never switch branches in a checkout you did not create.
- **The main checkout belongs to nobody.** Treat `~/projects/minspec/workbench` as read-only reference; do not commit or switch branches in it.
- **A worktree has one owner: its creator.** Do not edit, stage, or commit in a worktree you did not create.
- **Never delete branches.** After a merge, leave the branch and the record it carries.

## Serena and Mate in every worktree

The tracked `.serena/project.yml` and `.mcp.json` ride into every worktree, so each worktree is a complete agent workspace:

- **Serena** (symbol-aware source assist) activates per worktree path; the PHP backend is Phpactor (`php_phpactor`), which Serena manages as a PHAR — PHP ≥ 8.1, no Node required.
- **Mate** (`vendor/bin/mate`, the MCP server that serves this application's truth) is per-checkout: after `git worktree add`, run `composer install` before Mate tools exist in that worktree. Composer's global cache makes this cheap. The `symfony/ai-mate-composer-plugin` refreshes extension discovery on every install.
- A worktree where `composer install` has not run has Serena but not Mate; do not report Mate findings from a worktree that never installed.

## Boundaries

- No MinSpec doctrine is authored here; doctrine lives in `minspec/minspec` and `minspec/.github`. This host consumes it.
- `.env.local` and secret-bearing files are never committed. Composer auth stays in the machine's Composer home, never in the tree.
- Dependency changes (packages, plugins, constraints) are maintainer-approved per change, as everywhere in MinSpec.
- Generated caches (`var/`, `vendor/`) stay untracked; the lockfile is the record of what was validated.

## Validation duty

This is the integration oracle: a package or recipe claim is validated by installing and exercising it here, and the result is reported with the commands run and the lockfile state it was measured against. Never substitute confidence for an install that actually ran.
