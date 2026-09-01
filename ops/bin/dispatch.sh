#!/usr/bin/env bash
set -uo pipefail

refuse() {
    printf 'dispatch: refusal: %s\n' "$*" >&2
    exit 3
}

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
conf_path=${DISPATCH_CONF:-"$script_dir/dispatch.conf"}
[[ -f $conf_path ]] || refuse "expected a sourced conf; found no file at $conf_path; set DISPATCH_CONF to dispatch.conf"

# LAUNCHER_PIN is resolved after sourcing, not required here: by default the
# launcher is found relative to this script (portable across worktrees and
# machines, and no home path leaks into a public conf). An operator who wants
# the hard supply-chain lock may still set an absolute LAUNCHER_PIN in the conf.
keys=(DEFAULT_TIMEOUT WF_AGENT_DEFAULT MODEL_GROK MODEL_CODEX MODEL_CLAUDE_READ MODEL_CLAUDE_PLAN)
for key in "${keys[@]}"; do
    if [[ -v $key ]]; then
        printf -v "saved_$key" '%s' "${!key}"
        printf -v "had_$key" '%s' 1
    else
        printf -v "had_$key" '%s' 0
    fi
done
# shellcheck source=/dev/null
source "$conf_path" || refuse "expected a sourceable conf; found an error in $conf_path; fix its shell assignments"
for key in "${keys[@]}"; do
    had="had_$key"
    saved="saved_$key"
    if [[ ${!had} == 1 ]]; then
        printf -v "$key" '%s' "${!saved}"
    fi
    [[ -v $key ]] || refuse "expected conf key $key; found it unset; define $key in $conf_path"
done

# Resolve the launcher. An absolute LAUNCHER_PIN from the conf or environment
# wins (the operator's hard lock); otherwise default to this script's sibling
# launcher, so a fresh checkout or worktree runs without editing any path.
if [[ -z ${LAUNCHER_PIN:-} ]]; then
    LAUNCHER_PIN="$script_dir/../devlane/dispatch/launch.py"
fi
LAUNCHER_PIN=$(realpath -e -- "$LAUNCHER_PIN" 2>/dev/null) \
    || refuse "expected LAUNCHER_PIN to name a launcher; found none at the resolved path; place launch.py or set LAUNCHER_PIN"

branch=''
worktree=''
job=''
harness=''
model=''
unit=''
stage=''
scope_file=''
input=''
follows=''
timeout=$DEFAULT_TIMEOUT
agent=$WF_AGENT_DEFAULT
dry_run=0

need_value() {
    (($# >= 2)) || refuse "expected a value after $1; found end of arguments; provide $1 VALUE"
    [[ $2 != --* ]] || refuse "expected a value after $1; found $2; provide $1 VALUE"
}

while (($#)); do
    case $1 in
        --branch) need_value "$@"; branch=$2; shift 2 ;;
        --worktree) need_value "$@"; worktree=$2; shift 2 ;;
        --job) need_value "$@"; job=$2; shift 2 ;;
        --harness) need_value "$@"; harness=$2; shift 2 ;;
        --model) need_value "$@"; model=$2; shift 2 ;;
        --unit) need_value "$@"; unit=$2; shift 2 ;;
        --stage) need_value "$@"; stage=$2; shift 2 ;;
        --scope-file) need_value "$@"; scope_file=$2; shift 2 ;;
        --input) need_value "$@"; input=$2; shift 2 ;;
        --follows) need_value "$@"; follows=$2; shift 2 ;;
        --timeout) need_value "$@"; timeout=$2; shift 2 ;;
        --agent) need_value "$@"; agent=$2; shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        *) refuse "expected a documented flag; found unknown flag $1; remove it" ;;
    esac
done

[[ -n $job ]] || refuse "expected required --job; found it missing; pass --job JOB"
[[ -n $harness ]] || refuse "expected required --harness; found it missing; pass --harness grok, codex, or claude"
[[ -n $scope_file ]] || refuse "expected required --scope-file; found it missing; pass --scope-file FILE"
[[ -n $branch || -n $worktree ]] || refuse "expected one of --branch or --worktree; found neither; select a lineage worktree"
[[ -z $branch || -z $worktree ]] || refuse "expected one of --branch or --worktree; found both; pass exactly one"
case $harness in grok|codex|claude) ;; *) refuse "expected --harness grok, codex, or claude; found $harness; choose a supported harness" ;; esac
[[ -f $scope_file ]] || refuse "expected --scope-file to name a file; found $scope_file; create the file or correct the path"

scope_bytes=$(wc -c < "$scope_file") || refuse "expected a readable --scope-file; found unreadable $scope_file; correct its permissions"
scope_bytes=${scope_bytes//[[:space:]]/}
((scope_bytes <= 1024)) || refuse "expected --scope-file at most 1024 bytes; found $scope_bytes bytes; shorten it"
scope=$(cat -- "$scope_file"; printf x) || refuse "expected a readable --scope-file; found unreadable $scope_file; correct its permissions"
scope=${scope%x}

if [[ -n $branch ]]; then
    found_path=
    candidate=
    while IFS= read -r line; do
        case $line in
            'worktree '*) candidate=${line#worktree } ;;
            "branch refs/heads/$branch") found_path=$candidate; break ;;
        esac
    done < <(git worktree list --porcelain)
    [[ -n $found_path ]] || refuse "expected branch $branch in a worktree; found none; run git worktree add <path> $branch"
    worktree=$found_path
fi
[[ -d $worktree ]] || refuse "expected a worktree directory; found $worktree; correct --worktree"

ref=$(git -C "$worktree" rev-parse HEAD 2>/dev/null) || refuse "expected a git worktree; found $worktree without HEAD; correct the target"
lineage=$(git -C "$worktree" symbolic-ref --quiet --short HEAD 2>/dev/null || true)

if [[ -z $model ]]; then
    case $harness in
        grok) model=$MODEL_GROK ;;
        codex) model=$MODEL_CODEX ;;
        claude) if [[ $stage == plan ]]; then model=$MODEL_CLAUDE_PLAN; else model=$MODEL_CLAUDE_READ; fi ;;
    esac
fi

argv=("$LAUNCHER_PIN" "$job" --harness "$harness" --model "$model" --ref "$ref")
[[ -z $lineage ]] || argv+=(--lineage "$lineage")
[[ -z $unit ]] || argv+=(--unit "$unit")
[[ -z $stage ]] || argv+=(--stage "$stage")
argv+=(--scope "$scope")
[[ -z $input ]] || argv+=(--input "$input")
[[ -z $follows ]] || argv+=(--follows "$follows")

print_launch() {
    DEFAULT_TIMEOUT=$timeout WF_AGENT=$agent python3 -c 'import json, os, sys; print(json.dumps({"argv": sys.argv[1:], "env": {"DEFAULT_TIMEOUT": os.environ["DEFAULT_TIMEOUT"], "WF_AGENT": os.environ["WF_AGENT"]}}, ensure_ascii=False))' "${argv[@]}"
}

print_launch
((dry_run)) && exit 0

DEFAULT_TIMEOUT=$timeout WF_AGENT=$agent python3 "${argv[@]}"
rc=$?
printf 'LAUNCH EXIT %s\n' "$rc"
jobs_root=${DISPATCH_JOBS:-"${XDG_STATE_HOME:-$HOME/.local/state}/minspec/dispatch"}
newest=$(find "$jobs_root" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)
printf 'record: %s\n' "${newest:-unresolved: no dispatch record found under $jobs_root}"
exit "$rc"
