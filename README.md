# MinSpec Workbench

Maintainer-only, Mate-enabled host application for MinSpec.

This is the integration workbench: a real Symfony application where MinSpec packages and recipes are validated — installed, wired, and exercised — and the expected working environment for maintainer-directed development with AI assistance (Serena for source, Mate for application truth).

It is not a template, not a starter, and not a public contribution surface. Public visibility does not imply public governance, public write access, or an open contribution process; see the org `CONTRIBUTING.md`.

## Working here

```bash
# one line of work, one worktree (see AGENTS.md)
git worktree add ../wt/workbench/my-change -b my-change origin/main
cd ../wt/workbench/my-change
composer install          # Mate is per-checkout: vendor/bin/mate arrives here
vendor/bin/mate tools:list
```

The application scaffold is composed from `minspec/skeleton`; higher-level capabilities arrive as packages and recipes, installed deliberately.
