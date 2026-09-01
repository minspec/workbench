---
name: cross-review
description: Have the other two harnesses (Codex, Grok — or Claude when another harness leads) review work against a detached snapshot. Use before merging test-bearing or evidence-bearing changes.
---

Read and follow `ops/process/cross-review.md` — the canonical process
document. The two rules that must survive any summary: reviewers get a
detached snapshot, never the live worktree; and only the owner triggers
`@codex review` on GitHub — the local `codex exec` reviewer is a different
thing and is yours to run.
