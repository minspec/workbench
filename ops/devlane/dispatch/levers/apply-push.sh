#!/usr/bin/env bash
# apply-push.sh — custody-preserving apply/push bridge for isolated write jobs.
#
# Accepts only patch bytes and a caller-supplied message from a harness job,
# validates the patch path boundary, re-applies it in a fresh detached worktree,
# authors a controlled commit, runs named gates, and pushes without importing
# the job's git objects, environment, home, configuration, or credentials.
# Every failed precondition is a REFUSAL (exit 64); operational gate/apply/push
# failures are also recorded and fail closed. Evidence is retained under the
# jobs root even though the disposable worktree is always removed.
#
# Usage: see usage() below. A repository remote named "origin" is required.

set -u -o pipefail

readonly PROGRAM=${0##*/}
LEVERS_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) ||
    { printf '%s: refused: cannot resolve script directory\n' "$PROGRAM" >&2; exit 64; }
readonly LEVERS_DIR
readonly JOBS_ROOT_DEFAULT="$LEVERS_DIR/jobs"
readonly BRIDGE_NAME="Apply Push Bridge"
readonly BRIDGE_EMAIL="noreply@apply-push-bridge"
readonly SAFE_PATH="/usr/local/bin:/usr/bin:/bin"

refuse() {
    failure=${failure:-$*}
    printf '%s: REFUSED: %s\n' "$PROGRAM" "$*" >&2
    exit 64
}

usage() {
    cat <<'EOF'
apply-push.sh --from-job DIR | --patch FILE --base SHA
              --repo PATH --branch NAME
              [--message-file PATH | --message TEXT]
              [--gates "imports,lint,commit-trailers"]
              [--gate-runner "python3 .dev/app/workflow/wf.py check --verify-only {gate}"]
              --allow-paths GLOB [--allow-paths GLOB ...]
              [--protected "main,dev"] [--force-with-lease] [--dry-run]
              [--jobs-root DIR]
EOF
}

from_job="" patch_file="" repo="" branch="" base=""
message_text="" message_file="" gates="" protected="main,dev"
gate_runner="python3 .dev/app/workflow/wf.py check --verify-only {gate}"
jobs_root="$JOBS_ROOT_DEFAULT" force_lease="no" dry_run="no"
allow_paths=()
original_argv=("$@")

while (( $# )); do
    case "$1" in
        --from-job) from_job=${2:-}; shift 2 ;;
        --patch) patch_file=${2:-}; shift 2 ;;
        --repo) repo=${2:-}; shift 2 ;;
        --branch) branch=${2:-}; shift 2 ;;
        --base) base=${2:-}; shift 2 ;;
        --message) message_text=${2:-}; shift 2 ;;
        --message-file) message_file=${2:-}; shift 2 ;;
        --gates) gates=${2:-}; shift 2 ;;
        --gate-runner) gate_runner=${2:-}; shift 2 ;;
        --allow-paths) allow_paths+=("${2:-}"); shift 2 ;;
        --protected) protected=${2:-}; shift 2 ;;
        --force-with-lease) force_lease="yes"; shift ;;
        --dry-run) dry_run="yes"; shift ;;
        --jobs-root) jobs_root=${2:-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) refuse "unknown argument '$1'" ;;
    esac
done

[[ -n "$repo" && -n "$branch" ]] || refuse "--repo and --branch are required"
[[ "$branch" != -* && "$branch" != *$'\n'* ]] || refuse "invalid branch name '$branch'"
(( ${#allow_paths[@]} )) || refuse "at least one --allow-paths glob is required; there is no allow-all default"
if [[ -n "$from_job" && -n "$patch_file" ]] || [[ -z "$from_job" && -z "$patch_file" ]]; then
    refuse "give exactly one of --from-job DIR or --patch FILE"
fi
if [[ -n "$message_text" && -n "$message_file" ]]; then
    refuse "give at most one of --message or --message-file"
fi
IFS=',' read -r -a denied <<<"$protected"
for item in "${denied[@]}"; do
    [[ -z "$item" || "$branch" != "$item" ]] || refuse "branch '$branch' is protected"
done

resolved_repo=$(realpath -e -- "$repo" 2>/dev/null) || refuse "--repo '$repo' does not resolve"
git -C "$resolved_repo" rev-parse --git-dir >/dev/null 2>&1 || refuse "'$resolved_repo' is not a git repository"
git -C "$resolved_repo" check-ref-format --branch "$branch" >/dev/null 2>&1 || refuse "invalid branch name '$branch'"
git -C "$resolved_repo" remote get-url origin >/dev/null 2>&1 || refuse "repository has no origin remote"
[[ -n "${HOME:-}" ]] || refuse "HOME is unset; operator git custody cannot be resolved"
readonly OPERATOR_HOME=$HOME

mkdir -p -- "$jobs_root" || refuse "cannot create jobs root '$jobs_root'"
jobs_root=$(realpath -e -- "$jobs_root") || refuse "jobs root did not resolve"
job_id="$(date -u +%Y%m%dT%H%M%SZ)-apply-push-$(od -An -tx1 -N3 /dev/urandom | tr -d ' \n')"
record_dir="$jobs_root/$job_id"
mkdir -- "$record_dir" || refuse "cannot create evidence directory '$record_dir'"
patch_out="$record_dir/proposed.patch"
worktree="$record_dir/applied-worktree"
gate_results="$record_dir/gates.tsv"
: >"$gate_results"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_ref="" patch_sha="" remote_start="" new_sha="" push_result="not-attempted" failure=""

write_record() {
    ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    argv_file="$record_dir/argv.nul"
    printf '%s\0' "${original_argv[@]}" >"$argv_file"
    python3 - "$record_dir/run-record.json" "$job_id" "$argv_file" "$source_ref" "$base" \
        "$patch_sha" "$gate_results" "$new_sha" "$push_result" "$started" "$ended" \
        "$dry_run" "$failure" <<'PY'
import json, pathlib, sys
(out, job, argvf, source, base, patch_sha, gatesf, new_sha, push,
 started, ended, dry, failure) = sys.argv[1:]
raw = pathlib.Path(argvf).read_bytes().split(b"\0")
argv = [x.decode("utf-8", "surrogateescape") for x in raw if x]
results=[]
for line in pathlib.Path(gatesf).read_text(errors="replace").splitlines():
    name, rc, log = line.split("\t", 2)
    results.append({"name": name, "exit_code": int(rc), "passed": rc == "0", "log": log})
record={"job_id":job,"argv":argv,"source_job_ref":source or None,"base_sha":base or None,
 "patch_sha256":patch_sha or None,"gates":results,"new_sha":new_sha or None,
 "push_result":push,"dry_run":dry=="yes","failure":failure or None,
 "stamp":{"started":started,"ended":ended},
 "artifacts":{"patch":"proposed.patch","applied_worktree_log":"applied-worktree.log"}}
pathlib.Path(out).write_text(json.dumps(record, indent=2)+"\n")
PY
}

cleanup() {
    rc=$?
    if [[ -d "$worktree" ]]; then
        git -C "$worktree" log -1 --stat --decorate --format=fuller >"$record_dir/applied-worktree.log" 2>&1 || true
        git -C "$resolved_repo" worktree remove --force -- "$worktree" >/dev/null 2>&1 || true
    fi
    write_record 2>/dev/null || true
    exit "$rc"
}
trap cleanup EXIT

if [[ -n "$from_job" ]]; then
    resolved_job=$(realpath -e -- "$from_job" 2>/dev/null) || refuse "--from-job '$from_job' does not resolve"
    [[ -d "$resolved_job/wt" && -f "$resolved_job/run-record.json" ]] || refuse "source job lacks wt/ or run-record.json"
    readarray -t job_info < <(python3 - "$resolved_job/run-record.json" <<'PY'
import json,re,sys
r=json.load(open(sys.argv[1], encoding="utf-8"))
if r.get("runtime",{}).get("role") != "write": raise SystemExit(1)
s=r.get("scope",{}).get("source","")
m=re.search(r"\b[0-9a-fA-F]{40,64}\b",s)
c=r.get("write_clone",{}).get("post_run_commit",{}).get("sha") or ""
if not m or not c: raise SystemExit(1)
print(m.group(0)); print(c)
PY
    ) || refuse "source run record is not a completed write job with a recorded base"
    (( ${#job_info[@]} == 2 )) || refuse "source run record did not yield exactly base and commit"
    recorded_base=${job_info[0]}; source_commit=${job_info[1]}
    [[ -z "$base" || "$base" == "$recorded_base" ]] || refuse "--base does not match source job's recorded base"
    base=$recorded_base
    git -C "$resolved_job/wt" cat-file -e "${base}^{commit}" 2>/dev/null || refuse "recorded base is absent from source wt"
    [[ "$(git -C "$resolved_job/wt" rev-parse HEAD 2>/dev/null)" == "$source_commit" ]] || refuse "source wt HEAD no longer matches recorded lever commit"
    git -C "$resolved_job/wt" diff --binary --full-index "$base" HEAD -- >"$patch_out" || refuse "cannot derive patch from source job"
    source_ref="$resolved_job@$source_commit"
else
    [[ -n "$base" ]] || refuse "--patch requires --base SHA"
    resolved_patch=$(realpath -e -- "$patch_file" 2>/dev/null) || refuse "--patch '$patch_file' does not resolve"
    [[ -f "$resolved_patch" && -s "$resolved_patch" ]] || refuse "patch is not a non-empty regular file"
    cp -- "$resolved_patch" "$patch_out" || refuse "cannot retain patch bytes"
    source_ref="patch:$resolved_patch"
fi
[[ -s "$patch_out" ]] || refuse "resolved patch is empty; no output is not success"
patch_sha=$(sha256sum -- "$patch_out" | awk '{print $1}')

git -C "$resolved_repo" cat-file -e "${base}^{commit}" 2>/dev/null || refuse "base '$base' is not a commit in repo"
base=$(git -C "$resolved_repo" rev-parse "${base}^{commit}")
remote_start=$(env -i HOME="$OPERATOR_HOME" PATH="$SAFE_PATH" GIT_TERMINAL_PROMPT=0 \
    git -C "$resolved_repo" ls-remote --exit-code origin "refs/heads/$branch" 2>/dev/null | awk 'NR==1{print $1}') ||
    refuse "cannot observe origin branch '$branch'"
[[ -n "$remote_start" ]] || refuse "origin branch '$branch' has no tip"

# Ask git which paths it would apply, then also retain both names from every
# diff header (needed for rename/copy sources).  Keep producer statuses out of
# process substitutions: mapfile reports only its own status and would otherwise
# turn a parser failure into a partially populated, fail-open path list.
numstat="$record_dir/paths.numstat"
if ! git -C "$resolved_repo" apply --numstat -z "$patch_out" >"$numstat" 2>/dev/null; then
    refuse "patch path metadata cannot be parsed by git"
fi
touched_file="$record_dir/paths.touched"
if ! python3 - "$patch_out" "$numstat" "$touched_file" <<'PY'
import sys
paths=[]
def add(raw):
    if not raw: raise SystemExit(2)
    try: path=raw.decode("utf-8")
    except UnicodeDecodeError: raise SystemExit(2)
    if "\n" in path or "\r" in path: raise SystemExit(2)
    if path not in paths: paths.append(path)

for raw in open(sys.argv[1], "rb"):
    if raw.startswith(b"diff --git "):
        p=raw.rstrip(b"\n").split(b" ")
        if len(p)!=4 or not p[2].startswith(b"a/") or not p[3].startswith(b"b/"):
            raise SystemExit(2)
        add(p[2][2:]); add(p[3][2:])
    elif raw.startswith((b"rename from ", b"rename to ", b"copy from ", b"copy to ")):
        name=raw.rstrip(b"\n").split(b" ", 2)[2]
        if name.startswith(b'"'): raise SystemExit(2)
        add(name)
    elif raw.startswith((b"--- ", b"+++ ")):
        name=raw.rstrip(b"\n")[4:]
        if name == b"/dev/null": continue
        if name.startswith((b"a/", b"b/")): name=name[2:]
        if name.startswith(b'"') or b"\t" in name: raise SystemExit(2)
        add(name)

# --numstat -z is git apply's machine-readable view of the effective target,
# including /dev/null additions/deletions, mode-only, binary, and symlink diffs.
for record in open(sys.argv[2], "rb").read().split(b"\0"):
    if not record: continue
    fields=record.split(b"\t", 2)
    if len(fields)!=3: raise SystemExit(2)
    add(fields[2])
if not paths: raise SystemExit(3)
with open(sys.argv[3], "w", encoding="utf-8") as out:
    out.write("\n".join(paths)+"\n")
PY
then
    refuse "patch has malformed, quoted, non-UTF-8, or missing diff headers"
fi
mapfile -t touched <"$touched_file" || refuse "cannot read validated patch paths"

for path in "${touched[@]}"; do
    [[ -n "$path" && "$path" != /* && "$path" != .git && "$path" != .git/* ]] || refuse "unsafe patch path '$path'"
    IFS='/' read -r -a parts <<<"$path"
    for part in "${parts[@]}"; do [[ "$part" != ".." && "$part" != ".git" ]] || refuse "unsafe patch path '$path'"; done
    allowed="no"
    for pattern in "${allow_paths[@]}"; do
        [[ -n "$pattern" && "$pattern" != /* && "$pattern" != *".."* && "$pattern" != .git && "$pattern" != .git/* ]] || refuse "unsafe allow-paths pattern '$pattern'"
        # shellcheck disable=SC2053 # intentional: --allow-paths is a glob set
        if [[ "$path" == $pattern ]]; then allowed="yes"; break; fi
    done
    [[ "$allowed" == "yes" ]] || refuse "patch path '$path' is outside --allow-paths"
done

git -C "$resolved_repo" worktree add --quiet --detach "$worktree" "$base" || refuse "cannot create fresh worktree at base"
if ! git -C "$worktree" apply --3way --index --whitespace=error-all "$patch_out" >"$record_dir/apply.log" 2>&1; then
    refuse "patch does not apply cleanly (see apply.log)"
fi
git -C "$worktree" diff --cached --quiet && refuse "applied patch produced no change"

# Content guard: a clean allow-paths list still lets an agent land bytes it
# should not. Refuse absolute home paths, credential-file additions, and
# runtime job-capture leaking into a landing commit — the kinds of thing an
# agent adds by accident, not intent. This is defence in depth behind the
# path allow-list, not a substitute for it.
guard_report="$record_dir/content-guard.log"
if ! git -C "$worktree" diff --cached --unified=0 -- >"$record_dir/staged.diff" 2>/dev/null; then
    refuse "cannot read staged diff for content guard"
fi
python3 - "$record_dir/staged.diff" "$guard_report" <<'PY'
import re,sys
diff,report=sys.argv[1:]
added=[]           # (path, lineno_in_added_hunk, text)
path=None
for raw in open(diff,encoding="utf-8",errors="replace"):
    if raw.startswith("+++ b/"):
        path=raw[6:].rstrip("\n")
    elif raw.startswith("+") and not raw.startswith("+++"):
        added.append((path, raw[1:].rstrip("\n")))
hits=[]
# absolute POSIX home paths — machine-specific, private, never source truth
home=re.compile(r"/home/[A-Za-z0-9._-]+/")
# credential filenames introduced as content
cred=re.compile(r"\b(auth\.json|id_rsa|id_ed25519|\.pem|\.p12|credentials(\.json)?)\b")
for p,text in added:
    pth=p or "(unknown)"
    # runtime job capture path anywhere in an added line or as the file itself
    if "levers/jobs/" in (pth+" "+text):
        hits.append(f"{pth}: runtime job-capture content ('levers/jobs/')")
    if home.search(text):
        hits.append(f"{pth}: absolute home path in added content")
    if cred.search(text) or cred.search(pth):
        hits.append(f"{pth}: credential-file reference in added content")
seen=set(); uniq=[h for h in hits if not (h in seen or seen.add(h))]
open(report,"w",encoding="utf-8").write("\n".join(uniq))
sys.exit(3 if uniq else 0)
PY
guard_rc=$?
if [[ $guard_rc -ne 0 ]]; then
    first=$(head -1 "$guard_report" 2>/dev/null)
    refuse "content guard: landing commit carries forbidden bytes — ${first:-see content-guard.log}; found in staged diff; needed a clean patch or an explicit operator waiver"
fi

if [[ -n "$message_file" ]]; then
    resolved_message=$(realpath -e -- "$message_file" 2>/dev/null) || refuse "message file does not resolve"
    [[ -f "$resolved_message" && -s "$resolved_message" ]] || refuse "message file is empty or not regular"
    cp -- "$resolved_message" "$record_dir/message.input" || refuse "cannot copy message"
else
    [[ -n "${message_text//[$'\t\r\n ']/}" ]] || message_text="Apply isolated write job"
    printf '%s\n' "$message_text" >"$record_dir/message.input"
fi
if ! python3 - "$record_dir/message.input" "$record_dir/message.final" "$job_id" "$patch_sha" <<'PY'
import re,sys
src,out,job,digest=sys.argv[1:]
s=open(src,encoding="utf-8").read().rstrip()
if "\x00" in s or not s.strip(): raise SystemExit(1)
trailer=re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*: .+$")
caller_last=re.split(r"\n[ \t]*\n",s)[-1].splitlines()
separator="\n" if caller_last and all(trailer.match(x) for x in caller_last) else "\n\n"
s += f"{separator}Apply-Push-Job: {job}\nPatch-SHA256: {digest}\n"
paras=re.split(r"\n[ \t]*\n",s.rstrip())
last=paras[-1].splitlines()
if len(last)<2 or not all(trailer.match(x) for x in last): raise SystemExit(1)
open(out,"w",encoding="utf-8").write(s)
PY
then
    refuse "landing message/trailer block is not a contiguous final paragraph"
fi

if ! env -i HOME="$OPERATOR_HOME" PATH="$SAFE_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    git -C "$worktree" -c user.name="$BRIDGE_NAME" -c user.email="$BRIDGE_EMAIL" \
    commit --quiet --file "$record_dir/message.final" >"$record_dir/commit.log" 2>&1; then
    refuse "bridge could not author landing commit"
fi
new_sha=$(git -C "$worktree" rev-parse HEAD)

IFS=',' read -r -a gate_names <<<"$gates"
for gate in "${gate_names[@]}"; do
    [[ -z "$gate" ]] && continue
    [[ "$gate" =~ ^[A-Za-z0-9._-]+$ ]] || refuse "invalid gate name '$gate'"
    command_text=${gate_runner//\{gate\}/$gate}
    [[ "$command_text" != "$gate_runner" ]] || refuse "--gate-runner must contain {gate}"
    log="gate-$gate.log"
    # Gate commands are trusted repository policy, not job input. Execute in a
    # fixed, empty environment; no job home/config/environment is consulted.
    (cd -- "$worktree" && env -i HOME="$record_dir/gate-home" PATH="$SAFE_PATH" \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 GIT_TERMINAL_PROMPT=0 /bin/sh -c "$command_text") \
        >"$record_dir/$log" 2>&1
    gate_rc=$?
    printf '%s\t%s\t%s\n' "$gate" "$gate_rc" "$log" >>"$gate_results"
    [[ $gate_rc -eq 0 ]] || refuse "gate '$gate' failed (see $log)"
done

if git -C "$resolved_repo" merge-base --is-ancestor "$remote_start" "$new_sha" 2>/dev/null; then
    push_mode="plain"
else
    [[ "$force_lease" == "yes" ]] || refuse "landing is non-fast-forward; --force-with-lease is required"
    push_mode="lease"
fi
if [[ "$dry_run" == "yes" ]]; then
    push_result="dry-run:$push_mode"
else
    if [[ "$push_mode" == "plain" ]]; then
        env -i HOME="$OPERATOR_HOME" PATH="$SAFE_PATH" GIT_TERMINAL_PROMPT=0 \
            git -C "$worktree" push --porcelain origin "HEAD:refs/heads/$branch" >"$record_dir/push.log" 2>&1
    else
        env -i HOME="$OPERATOR_HOME" PATH="$SAFE_PATH" GIT_TERMINAL_PROMPT=0 \
            git -C "$worktree" push --porcelain --force-with-lease="refs/heads/$branch:$remote_start" \
            origin "HEAD:refs/heads/$branch" >"$record_dir/push.log" 2>&1
    fi
    push_rc=$?
    [[ $push_rc -eq 0 ]] || { push_result="failed:$push_mode"; refuse "push failed or lease was stale (see push.log)"; }
    push_result="pushed:$push_mode"
fi

printf '%s: job=%s new=%s push=%s evidence=%s\n' "$PROGRAM" "$job_id" "$new_sha" "$push_result" "$record_dir"
exit 0
