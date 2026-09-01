# dispatch/levers — custody-isolated harness dispatch (preserved)

Working levers + apply-push bridge, preserved from session 62d2e497 (Opus 4.8)
for the Fable handoff, so they survive the session.

- `grok-dispatch.sh`         — Grok, headless (investigation)
- `codex/codex-dispatch.sh`  — Codex, headless (implementation/security)
- `claude/fable-dispatch.sh` — Claude Code/Fable, headless, read/write; defaults
  to `claude-fable-5`. Claude is isolated by the canonical three CLI flags and
  carries no auth files because `HARNESSES["claude"].auth_files` is empty.
  Write jobs leave a lever-created commit in `wt/` and the run-record fields
  consumed unchanged by `apply-push.sh --from-job`. Provisioned `.levers/` are
  locally excluded in the write clone and cannot enter that commit.
- `claude/selftest.sh` — offline refusal and write-commit checks; it proves the
  model is never invoked for missing scope, HOME scope, empty prompt, or a
  missing auth-file declaration, and proves a provisioned write commit omits
  `.levers/`.
- `apply-push.sh` + `selftest.sh` — the trusted bridge that lands lever-produced
  patches (runs gates + validates trailers). Hardened through 4 real bugs.

`fable-dispatch.sh --provision-levers` installs Grok, Codex (with its vendored
isolation law), and the bridge selftest under the job workspace's `.levers/`.
It deliberately excludes `apply-push.sh`: dispatched custody may conduct more
isolated jobs but cannot push. Landing remains conductor-side.

The conductor verified the read-role argv live on 2026-08-28 with the operator
host's `~/.local/bin/claude`: the default Fable model, `plan` permission mode,
text output, and the three canonical isolation flags exited 0 in 8 seconds with
the correct answer; the run record reported flags isolation and unchanged
operator home. The write role's `acceptEdits` path remains unverified live.

TODO:
- de-vendor: import the real `ops/devlane/harness/isolation.py` instead of the copy
  under `codex/vendor/`.
- proper mini-app wiring: CONTRACT, CI job, contract-tests.

Full provenance/context: `.dev/xor/handoff/HANDOFF-fable-2026-08-27.md`
