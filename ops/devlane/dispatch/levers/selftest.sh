#!/usr/bin/env bash
# selftest.sh — offline acceptance/refusal tests for apply-push.sh.
# Builds only throwaway repositories beneath mktemp and uses a local bare
# repository as the real push/lease endpoint. No network or caller repo is used.

set -u -o pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
readonly BRIDGE="$HERE/apply-push.sh"
tmp=$(mktemp -d) || exit 1
trap 'rm -rf -- "$tmp"' EXIT
export GIT_CONFIG_NOSYSTEM=1
export HOME="$tmp/home"
mkdir -p "$HOME"
passed=0 total=0

pass() { passed=$((passed+1)); total=$((total+1)); printf 'PASS: %s\n' "$1"; }
fail() { total=$((total+1)); printf 'FAIL: %s\n' "$1"; }
assert_eq() { [[ "$1" == "$2" ]]; }

git init --bare --quiet "$tmp/remote.git"
git init --quiet "$tmp/source"
git -C "$tmp/source" config user.name Test
git -C "$tmp/source" config user.email test@example.invalid
mkdir -p "$tmp/source/allowed"
printf 'base\n' >"$tmp/source/allowed/data.txt"
printf 'outside\n' >"$tmp/source/outside.txt"
git -C "$tmp/source" add -A
git -C "$tmp/source" commit --quiet -m base
base=$(git -C "$tmp/source" rev-parse HEAD)
git -C "$tmp/source" branch feature
git -C "$tmp/source" branch dev
git -C "$tmp/source" remote add origin "$tmp/remote.git"
git -C "$tmp/source" push --quiet origin feature dev

make_job() {
    name=$1 path=$2 content=$3
    dir="$tmp/$name"; mkdir -p "$dir"
    git clone --quiet "$tmp/source" "$dir/wt"
    git -C "$dir/wt" checkout --quiet --detach "$base"
    mkdir -p "$(dirname "$dir/wt/$path")"
    printf '%s\n' "$content" >"$dir/wt/$path"
    git -C "$dir/wt" -c user.name=Lever -c user.email=lever@example.invalid add -A
    git -C "$dir/wt" -c user.name=Lever -c user.email=lever@example.invalid commit --quiet -m "lever commit"
    sha=$(git -C "$dir/wt" rev-parse HEAD)
    python3 - "$dir/run-record.json" "$base" "$sha" <<'PY'
import json,sys
json.dump({"scope":{"source":"ref feature (%s) archived"%sys.argv[2]},
 "runtime":{"role":"write"},"write_clone":{"post_run_commit":{"sha":sys.argv[3]}}},open(sys.argv[1],"w"))
PY
    printf '%s' "$dir"
}

run_bridge() { "$BRIDGE" --repo "$tmp/source" --branch feature --jobs-root "$tmp/evidence" \
    --allow-paths 'allowed/**' --gates stub --gate-runner "$1 {gate}" "${@:2}" >/dev/null 2>&1; }
remote_tip() { git --git-dir="$tmp/remote.git" rev-parse "refs/heads/$1"; }

job=$(make_job github .github/workflows/x.yml ci)
if "$BRIDGE" --repo "$tmp/source" --branch feature --from-job "$job" --allow-paths '.github/**' \
    --jobs-root "$tmp/evidence" --gates stub --gate-runner 'true {gate}' --dry-run >/dev/null 2>&1; then
    pass ".github/** allow-paths accepted"
else
    fail ".github/** allow-paths accepted"
fi

before=$(remote_tip feature)
if ! "$BRIDGE" --repo "$tmp/source" --branch feature --from-job "$job" --allow-paths '.git/**' \
    --jobs-root "$tmp/evidence" --gates stub --gate-runner 'true {gate}' --dry-run >/dev/null 2>&1 \
    && assert_eq "$(remote_tip feature)" "$before"; then
    pass ".git/** allow-paths refused without push"
else
    fail ".git/** allow-paths refused without push"
fi

job=$(make_job happy allowed/data.txt landed)
if run_bridge true --from-job "$job" --message $'Land tested change\n\nSource: isolated-write-job\nCo-Authored-By: Lever <lever@example.invalid>'; then
    tip=$(remote_tip feature)
    msg=$(git --git-dir="$tmp/remote.git" show -s --format=%B "$tip")
    treeval=$(git --git-dir="$tmp/remote.git" show "$tip:allowed/data.txt")
    if [[ "$treeval" == landed ]] && python3 - "$msg" <<'PY'
import re,sys
p=re.split(r"\n\s*\n",sys.argv[1].strip())[-1].splitlines()
raise SystemExit(not (len(p)==4 and all(re.match(r"^[A-Za-z0-9-]+: .+",x) for x in p)))
PY
    then pass "happy path lands expected tree with contiguous trailers"; else fail "happy path lands expected tree with contiguous trailers"; fi
else fail "happy path lands expected tree with contiguous trailers"; fi

parsed=$(git --git-dir="$tmp/remote.git" show -s --format=%B "$tip" | git interpret-trailers --parse)
if [[ "$parsed" == *'Source: isolated-write-job'* ]] \
    && [[ "$parsed" == *'Co-Authored-By: Lever <lever@example.invalid>'* ]] \
    && [[ "$parsed" == *'Apply-Push-Job: '* ]] \
    && [[ "$parsed" == *'Patch-SHA256: '* ]]; then
    pass "caller Source survives as git-parsed trailer after landing"
else
    fail "caller Source survives as git-parsed trailer after landing"
fi

job=$(make_job outside outside.txt changed); before=$(remote_tip feature)
if ! run_bridge true --from-job "$job" && assert_eq "$(remote_tip feature)" "$before"; then pass "outside allow-path refused without push"; else fail "outside allow-path refused without push"; fi

# The advertised diff paths are allowed, but git apply follows ---/+++ and
# would modify outside.txt unless its effective target is independently parsed.
job=$(make_job headerlie outside.txt headerlie)
lie="$tmp/header-lie.patch"
git -C "$job/wt" diff --binary "$base" HEAD -- >"$lie"
sed -i '1s@a/outside.txt b/outside.txt@a/allowed/data.txt b/allowed/data.txt@' "$lie"
before=$(remote_tip feature)
if ! run_bridge true --patch "$lie" --base "$base" && assert_eq "$(remote_tip feature)" "$before"; then pass "mismatched diff target refused without push"; else fail "mismatched diff target refused without push"; fi

# A raw malicious diff header proves traversal/.git syntax is rejected before apply.
bad="$tmp/bad.patch"; printf 'diff --git a/../.git/config b/../.git/config\n' >"$bad"; before=$(remote_tip feature)
if ! run_bridge true --patch "$bad" --base "$base" && assert_eq "$(remote_tip feature)" "$before"; then pass "dotdot/.git path refused without push"; else fail "dotdot/.git path refused without push"; fi

# git apply accepts this trailing line as harmless text.  The path parser exits
# non-zero after emitting the valid path; process-substitution mapfile used to
# swallow that failure and let the valid change continue to the push.
job=$(make_job parserfail allowed/data.txt parserfail)
malformed="$tmp/parser-failure.patch"
git -C "$job/wt" diff --binary "$base" HEAD -- >"$malformed"
printf 'diff --git malformed\n' >>"$malformed"
before=$(remote_tip feature)
if ! run_bridge true --patch "$malformed" --base "$base" && assert_eq "$(remote_tip feature)" "$before"; then pass "path parser failure refused without push"; else fail "path parser failure refused without push"; fi

job=$(make_job gatefail allowed/data.txt gatefail); before=$(remote_tip feature)
if ! run_bridge false --from-job "$job" && assert_eq "$(remote_tip feature)" "$before"; then pass "failing gate refused without push"; else fail "failing gate refused without push"; fi

job=$(make_job protected allowed/data.txt protected); before=$(remote_tip dev)
if ! "$BRIDGE" --repo "$tmp/source" --branch dev --from-job "$job" --allow-paths 'allowed/**' --jobs-root "$tmp/evidence" >/dev/null 2>&1 && assert_eq "$(remote_tip dev)" "$before"; then pass "protected dev refused without push"; else fail "protected dev refused without push"; fi

# Remote feature now contains happy commit, while this job is based on original base.
job=$(make_job nonff allowed/data.txt nonff); before=$(remote_tip feature)
if ! run_bridge true --from-job "$job" && assert_eq "$(remote_tip feature)" "$before"; then pass "non-fast-forward refused without force-with-lease"; else fail "non-fast-forward refused without force-with-lease"; fi

# The gate advances the remote after observation; exact lease must reject the push.
advance="$tmp/advance.sh"
cat >"$advance" <<EOF
#!/bin/sh
git --git-dir='$tmp/remote.git' update-ref refs/heads/feature '$base'
EOF
chmod +x "$advance"
job=$(make_job lease allowed/data.txt lease); observed=$(remote_tip feature)
if ! run_bridge "$advance" --from-job "$job" --force-with-lease && [[ "$(remote_tip feature)" == "$base" && "$(remote_tip feature)" != "$observed" ]]; then pass "lease mismatch rejects concurrent overwrite"; else fail "lease mismatch rejects concurrent overwrite"; fi

# Restore, then prove dry-run reaches a valid FF result but does not move it.
git --git-dir="$tmp/remote.git" update-ref refs/heads/feature "$base"
job=$(make_job dryrun allowed/data.txt dryrun); before=$(remote_tip feature)
if run_bridge true --from-job "$job" --dry-run && assert_eq "$(remote_tip feature)" "$before"; then pass "dry-run leaves remote ref unmoved"; else fail "dry-run leaves remote ref unmoved"; fi

printf 'SELFTEST: %s/%s passed\n' "$passed" "$total"
[[ "$passed" -eq "$total" ]]
