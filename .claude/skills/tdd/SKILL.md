---
name: tdd
description: The wf-governed red→green loop — honest reds, sealed frozen sets, independent tests. Use when implementing anything under a tdd gate kind, or starting test-first work.
---

Read and follow `ops/process/tdd.md` — it is the canonical process document,
shared by every harness that works this repo (Claude, Codex, Grok), so it is
not duplicated here. When a step calls for the test-author or test-skeptic
role, prefer a different harness via `ops/process/cross-review.md`; the
`test-author` and `test-skeptic` subagents are the fallback when you must
fill a role in-harness.
