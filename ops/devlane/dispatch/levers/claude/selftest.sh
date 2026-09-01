#!/usr/bin/env bash
# Offline refusal and write-commit tests. A fake claude marks invocation; each
# refusal must precede it, while the positive case uses it to make one change.
set -u -o pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
readonly LEVER="$HERE/fable-dispatch.sh"
tmp=$(mktemp -d) || exit 1
cleanup() { chmod -R u+w -- "$tmp" 2>/dev/null || true; rm -rf -- "$tmp"; }
trap cleanup EXIT
real_home=${HOME:-}; export HOME="$tmp/home"; mkdir -p "$HOME" "$tmp/bin" "$tmp/scope"
printf 'scope\n' >"$tmp/scope/file"
marker="$tmp/model-invoked"
printf '#!/bin/sh\n: >"%s"\nexit 99\n' "$marker" >"$tmp/bin/claude"; chmod +x "$tmp/bin/claude"
export PATH="$tmp/bin:/usr/bin:/bin"
passed=0; total=0
check_refusal() {
    name=$1; shift; total=$((total+1)); rm -f -- "$marker"
    "$@" >"$tmp/out" 2>"$tmp/err"; rc=$?
    if [[ $rc -eq 64 && ! -e "$marker" ]] && grep -q 'REFUSED:' "$tmp/err"; then
        passed=$((passed+1)); printf 'PASS: %s\n' "$name"
    else printf 'FAIL: %s (rc=%s, stderr=%s)\n' "$name" "$rc" "$(<"$tmp/err")"; fi
}
check_refusal "no scope" "$LEVER" --prompt x
check_refusal "scope resolving to HOME" "$LEVER" --scope-path "$HOME" --prompt x
check_refusal "empty prompt" "$LEVER" --scope-path "$tmp/scope" --prompt '   '
mkdir -p "$tmp/orphan"; cp -- "$LEVER" "$tmp/orphan/fable-dispatch.sh"; chmod +x "$tmp/orphan/fable-dispatch.sh"
check_refusal "missing auth file declaration (canonical law absent)" "$tmp/orphan/fable-dispatch.sh" --scope-path "$tmp/scope" --prompt x

total=$((total+1)); rm -f -- "$marker"
printf '#!/bin/sh\nprintf "model change\\n" > model-change.txt\n: >"%s"\nexit 0\n' "$marker" >"$tmp/bin/claude"
jobs_root="$tmp/jobs"
"$LEVER" --scope-path "$tmp/scope" --prompt x --role write \
    --provision-levers --jobs-root "$jobs_root" >"$tmp/out" 2>"$tmp/err"; rc=$?
job_dir=$(find "$jobs_root" -mindepth 1 -maxdepth 1 -type d -print -quit 2>/dev/null)
if [[ $rc -eq 0 && -e "$marker" && -n "$job_dir" ]] &&
        git -C "$job_dir/wt" diff-tree --no-commit-id --name-only -r HEAD | grep -qx 'model-change.txt' &&
        ! git -C "$job_dir/wt" diff-tree --no-commit-id --name-only -r HEAD | grep -q '^\.levers/'; then
    passed=$((passed+1)); printf 'PASS: provisioned write commit excludes .levers\n'
else
    printf 'FAIL: provisioned write commit excludes .levers (rc=%s, stderr=%s)\n' "$rc" "$(<"$tmp/err")"
fi

printf 'SELFTEST: %s/%s passed; fake model invocations: 1\n' "$passed" "$total"
[[ "$passed" -eq "$total" ]]
export HOME=$real_home
