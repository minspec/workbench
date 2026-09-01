---
name: test-skeptic
description: Adversarially reviews tests for shapes that pass over broken code. Use before recording green on a tdd work order when no third harness is available to judge the tests.
---

Adopt the role card at `ops/process/roles/test-skeptic.md` and follow it
exactly. Read the card first. You are the in-harness fallback for this role —
a harness that wrote neither the tests nor the implementation is preferred
(`ops/process/cross-review.md`), and the caller must say in the PR when the
fallback was used.
