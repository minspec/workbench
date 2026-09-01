#!/bin/sh
set -eu

# Vendored from the Agent-Lab containment check, with selectors for this tree.
# Tests are fixtures for this checker, so they are deliberately excluded.
fail=0
warn=0
files=$(git ls-files -- 'ops/devlane/infra/' '.github/workflows/' \
    | while IFS= read -r path; do
        case "$path" in
            ops/devlane/infra/tests/*) ;;
            *) printf '%s\n' "$path" ;;
        esac
    done)

report_fail() {
    printf 'FAIL %s: %s\n' "$1" "$2"
    fail=$((fail + 1))
}

for path in $files; do
    [ -f "$path" ] || continue
    if grep -Eq '/var/run/docker\.sock|/run/docker\.sock' "$path"; then
        report_fail "$path" 'docker socket mount'
    fi
    if grep -Eq '(^|[[:space:]])privileged:[[:space:]]*true([[:space:]]|$)|--privileged([[:space:]]|$)' "$path"; then
        report_fail "$path" 'privileged'
    fi
    if grep -Eq 'network_mode:[[:space:]]*host([[:space:]]|$)|--network([=[:space:]]+)host([[:space:]]|$)' "$path"; then
        report_fail "$path" 'host networking'
    fi
    if grep -Eq 'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}' "$path"; then
        report_fail "$path" 'likely-real secret'
    fi
done

printf '%s fail, %s warn\n' "$fail" "$warn"
[ "$fail" -eq 0 ]
