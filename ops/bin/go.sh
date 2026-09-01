#!/usr/bin/env bash
# go.sh — the one-line way to hand work to the lane, so delegating is
# easier than grinding it inline. Everything dispatch.sh needs — harness,
# model, scope file, worktree resolution — is defaulted here from a short
# intent word. Read-only jobs stay read-only; the firewall is baked into
# the harness defaults (no producer owns two consecutive artifacts).
#
#   go.sh <intent> <branch|.> <scope sentence ...>
#
# intents (default harness in parens):
#   sweep      (codex)  security/defect sweep over disjoint categories
#   audit      (grok)   adversarial review of a change — findings that survive
#   review     (both)   sweep + audit, the review lane in one word
#   plan       (claude) a plan for the scope, no edits
#   tests      (claude) author tests from the contract, red-first
#   check      (codex)  check existing tests against the contract
#   implement  (claude) implement to green
#   adjudicate (grok)   adjudicate a contested result
#
# Override the harness with HARNESS=grok|codex|claude. Everything prints
# where its verdict landed; nothing is cached.
set -uo pipefail

here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
die() { printf 'go: %s\n' "$*" >&2; exit 64; }

(( $# >= 3 )) || die "usage: go.sh <intent> <branch|.> <scope sentence ...>"
intent=$1; where=$2; shift 2
scope="$*"

# intent -> job + default harness. review is the one that fans out.
case $intent in
    sweep)      job=sweep;              def_harness=codex  ;;
    audit)      job=adversarial-review; def_harness=grok   ;;
    plan)       job=plan;               def_harness=claude; stage=plan ;;
    tests)      job=author-tests;       def_harness=claude ;;
    check)      job=check-tests;        def_harness=codex  ;;
    implement)  job=implement;          def_harness=claude ;;
    adjudicate) job=adjudicate;         def_harness=grok   ;;
    review)     job=__fanout__;         def_harness=      ;;
    *) die "unknown intent '$intent'; see the header of $here/go.sh" ;;
esac

# where: '.' means this checkout; else a branch name in some worktree.
if [[ $where == . ]]; then
    root=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) \
        || die "'.' given but $PWD is not inside a git worktree"
    locator=(--worktree "$root")
else
    locator=(--branch "$where")
fi

# scope must fit the launcher's 1024-byte scope-file cap.
(( ${#scope} <= 1024 )) || die "scope is ${#scope} bytes; the cap is 1024 — shorten it"
scope_file=$(mktemp -t go-scope.XXXXXX) || die "cannot create scope file"
trap 'rm -f "$scope_file"' EXIT
printf '%s' "$scope" >"$scope_file"

fire() {  # fire <job> <harness> [<stage>]
    local j=$1 h=${2:-} st=${3:-}
    h=${HARNESS:-$h}
    [[ -n $h ]] || die "no harness for job '$j'; set HARNESS=grok|codex|claude"
    printf '>> go: %s  job=%s harness=%s  target=%s\n' "$intent" "$j" "$h" "$where" >&2
    local args=(--job "$j" --harness "$h" --scope-file "$scope_file" "${locator[@]}")
    [[ -n $st ]] && args+=(--stage "$st")
    bash "$here/dispatch.sh" "${args[@]}"
}

if [[ $job == __fanout__ ]]; then
    # The review lane: security sweep (codex) then adversarial audit (grok).
    # Two harnesses, so no single producer owns the whole review.
    rc=0
    fire sweep              codex || rc=$?
    fire adversarial-review grok  || rc=$?
    exit "$rc"
fi
fire "$job" "$def_harness" "${stage:-}"
