#!/usr/bin/env bash
# fable-dispatch.sh — custody-isolated headless Claude Code/Fable lever.
#
# Runs exactly one `claude -p` turn against an archived/copy snapshot. Read
# jobs see a read-only snapshot; write jobs edit a separate wt/ clone and this
# lever commits their changes after Claude exits. The resulting record exposes
# scope.source, runtime.role, and write_clone.post_run_commit.sha exactly as
# apply-push.sh --from-job consumes them.
#
# Claude's canonical isolation entry uses FLAGS, not a relocated home. Its
# auth_files list is empty: no credential is copied or linked from $HOME, and
# HOME stays the operator home so Claude's own authentication remains usable.
# The only operator setup suppressed is exactly what isolation.py records:
# --setting-sources project,local --strict-mcp-config
# --disable-slash-commands. No other operator-home path is hardcoded.
#
# --provision-levers makes <workspace>/.levers/ contain grok-dispatch.sh,
# codex/ (including its vendor), and selftest.sh, allowing Fable to conduct
# Grok/Codex jobs. apply-push.sh is deliberately NEVER provisioned: a job may
# conduct, but cannot hold push custody. Landing remains conductor-side.
#
# The read-role argv was verified live by the conductor on the operator host
# (2026-08-28, ~/.local/bin/claude): claude -p --model claude-fable-5
# --permission-mode plan --output-format text --setting-sources project,local
# --strict-mcp-config --disable-slash-commands. It exited 0 in 8s with the
# correct answer; the record reported flags isolation and an unchanged operator
# home. The write role's acceptEdits path has not yet been verified live.
#
# Usage: fable-dispatch.sh (--scope-ref REF --repo PATH | --scope-path DIR)
#          (--prompt TEXT | --prompt-file PATH)
#          [--model MODEL] [--role read|write] [--timeout SECONDS]
#          [--jobs-root DIR] [--record-evidence] [--provision-levers]
#
# Job: <jobs-root>/<id>/{snapshot/,wt/ (write),prompt.txt,stdout.log,
# stderr.log,argv.txt,run-record.json}

set -u -o pipefail

readonly PROGRAM=${0##*/}
LEVERS_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P) ||
    { printf '%s: REFUSED: cannot resolve levers directory\n' "$PROGRAM" >&2; exit 64; }
readonly LEVERS_DIR
readonly ISOLATION_PY="$LEVERS_DIR/../../harness/isolation.py"
readonly JOBS_ROOT_DEFAULT="$LEVERS_DIR/claude/jobs"

refuse() { printf '%s: REFUSED: %s\n' "$PROGRAM" "$*" >&2; exit 64; }
usage() {
    sed -n 's/^# Usage: //p; s/^#          /          /p' "${BASH_SOURCE[0]}"
}

scope_ref=""; scope_repo=""; scope_path=""; prompt_text=""; prompt_file=""
model="claude-fable-5"; role="read"; wall_timeout="300"
jobs_root="$JOBS_ROOT_DEFAULT"; record_evidence="no"; provision_levers="no"
readonly AGENT_NAME="Claude Fable 5"
readonly AGENT_EMAIL="noreply@anthropic.com"
readonly AGENT_IDENTITY="$AGENT_NAME <$AGENT_EMAIL>"

while (( $# )); do
    case "$1" in
        --scope-ref) scope_ref=${2:-}; shift 2 ;;
        --repo) scope_repo=${2:-}; shift 2 ;;
        --scope-path) scope_path=${2:-}; shift 2 ;;
        --prompt) prompt_text=${2:-}; shift 2 ;;
        --prompt-file) prompt_file=${2:-}; shift 2 ;;
        --model) model=${2:-}; shift 2 ;;
        --role) role=${2:-}; shift 2 ;;
        --timeout) wall_timeout=${2:-}; shift 2 ;;
        --jobs-root) jobs_root=${2:-}; shift 2 ;;
        --record-evidence) record_evidence="yes"; shift ;;
        --provision-levers) provision_levers="yes"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) refuse "unknown argument '$1'" ;;
    esac
done

[[ -n "${HOME:-}" ]] || refuse "HOME is unset or empty; the operator/isolation boundary cannot be resolved"
readonly OPERATOR_HOME=$HOME
[[ -f "$ISOLATION_PY" && -r "$ISOLATION_PY" ]] ||
    refuse "canonical isolation law missing at '$ISOLATION_PY'; its claude auth_files declaration cannot be verified"

if [[ -n "$scope_ref" && -n "$scope_path" ]]; then
    refuse "give exactly one of --scope-ref or --scope-path, not both"
fi
if [[ -z "$scope_ref" && -z "$scope_path" ]]; then
    refuse "no scope given: pass --scope-ref REF --repo PATH, or --scope-path DIR — there is no default scope, and the default is never the live repo or \$HOME"
fi
if [[ -n "$scope_ref" ]]; then
    [[ -n "$scope_repo" ]] || refuse "--scope-ref requires --repo PATH naming the repository to archive it from"
    resolved_repo=$(realpath -e -- "$scope_repo" 2>/dev/null) || refuse "--repo '$scope_repo' does not resolve to an existing path"
    git -C "$resolved_repo" rev-parse --git-dir >/dev/null 2>&1 || refuse "'$resolved_repo' is not a git repository"
    ref_sha=$(git -C "$resolved_repo" rev-parse --verify --end-of-options "${scope_ref}^{commit}" 2>&1) ||
        refuse "--scope-ref '$scope_ref' does not resolve to a commit in '$resolved_repo': $ref_sha"
    SNAPSHOT_MODE="ref"; RESOLVED_REPO=$resolved_repo; REF_SHA=$ref_sha
else
    resolved_scope=$(realpath -e -- "$scope_path" 2>/dev/null) || refuse "--scope-path '$scope_path' does not resolve to an existing path"
    [[ -d "$resolved_scope" ]] || refuse "--scope-path '$resolved_scope' is not a directory"
    resolved_home=$(realpath -e -- "$OPERATOR_HOME" 2>/dev/null) || resolved_home=$OPERATOR_HOME
    [[ "$resolved_scope" != "$resolved_home" ]] || refuse "--scope-path resolves to the operator's \$HOME ($resolved_home); this is never permitted, no override exists"
    [[ "$resolved_home" != "$resolved_scope"/* ]] || refuse "--scope-path '$resolved_scope' is an ancestor of the operator's \$HOME; this is never permitted"
    for guard in .ssh .aws .gnupg .grok .claude .codex .config; do
        [[ "$resolved_scope" != "$resolved_home/$guard" && "$resolved_scope" != "$resolved_home/$guard"/* ]] ||
            refuse "--scope-path '$resolved_scope' is inside the operator's '$guard' directory; refusing"
    done
    SNAPSHOT_MODE="path"; RESOLVED_SCOPE=$resolved_scope; REF_SHA=""
fi

if [[ -n "$prompt_text" && -n "$prompt_file" ]]; then refuse "give exactly one of --prompt or --prompt-file, not both"; fi
if [[ -z "$prompt_text" && -z "$prompt_file" ]]; then refuse "no prompt given: pass --prompt TEXT or --prompt-file PATH"; fi
if [[ -n "$prompt_file" ]]; then
    resolved_prompt_file=$(realpath -e -- "$prompt_file" 2>/dev/null) || refuse "--prompt-file '$prompt_file' does not resolve to an existing file"
    [[ -f "$resolved_prompt_file" && -s "$resolved_prompt_file" ]] || refuse "--prompt-file '$resolved_prompt_file' is not a non-empty regular file"
fi
if [[ -n "$prompt_text" ]]; then
    trimmed=${prompt_text//[$'\t\r\n ']/}; [[ -n "$trimmed" ]] || refuse "--prompt is empty or whitespace-only; refusing rather than dispatching an empty job"
fi
case "$role" in read|write) ;; *) refuse "--role '$role' is not one of read|write" ;; esac
[[ -n "$model" ]] || refuse "--model must not be empty"
[[ "$wall_timeout" =~ ^[0-9]+$ && "$wall_timeout" -gt 0 ]] || refuse "--timeout '$wall_timeout' must be a positive integer number of seconds"

claude_bin=$(command -v claude 2>/dev/null || true)
[[ -n "$claude_bin" && -x "$claude_bin" ]] || refuse "no executable 'claude' binary found on PATH"
readonly CLAUDE_BIN=$claude_bin

mkdir -p -- "$jobs_root" || refuse "cannot create jobs root '$jobs_root'"
jobs_root=$(realpath -e -- "$jobs_root") || refuse "jobs root '$jobs_root' did not resolve after creation"
job_id="$(date -u +%Y%m%dT%H%M%SZ)-fable-$(od -An -tx1 -N3 /dev/urandom | tr -d ' \n')"
job_dir="$jobs_root/$job_id"
mkdir -- "$job_dir" || refuse "job directory '$job_dir' already exists or could not be created"
mkdir -- "$job_dir/snapshot" || refuse "cannot create snapshot under '$job_dir'"

if [[ "$SNAPSHOT_MODE" == "ref" ]]; then
    if ! git -C "$RESOLVED_REPO" archive --format=tar "$REF_SHA" 2>"$job_dir/.archive.err" | tar -x -C "$job_dir/snapshot"; then
        refuse "snapshot: git archive of $REF_SHA failed: $(<"$job_dir/.archive.err")"
    fi
    snapshot_source_desc="ref $scope_ref ($REF_SHA) archived from $RESOLVED_REPO"
else
    if ! rsync -a --exclude='.git' -- "$RESOLVED_SCOPE"/ "$job_dir/snapshot"/ 2>"$job_dir/.rsync.err"; then
        refuse "snapshot: copying '$RESOLVED_SCOPE' failed: $(<"$job_dir/.rsync.err")"
    fi
    snapshot_source_desc="path $RESOLVED_SCOPE (copied)"
fi
rm -f -- "$job_dir/.archive.err" "$job_dir/.rsync.err"
[[ -n "$(find "$job_dir/snapshot" -mindepth 1 -print -quit 2>/dev/null)" ]] || refuse "snapshot at '$job_dir/snapshot' is empty ($snapshot_source_desc); refusing"

write_clone_desc=""; claude_cwd="$job_dir/snapshot"
if [[ "$role" == "write" ]]; then
    if [[ "$SNAPSHOT_MODE" == "ref" ]]; then
        git clone --no-hardlinks --quiet -- "$RESOLVED_REPO" "$job_dir/wt" 2>"$job_dir/.clone.err" || refuse "write side clone failed: $(<"$job_dir/.clone.err")"
        git -C "$job_dir/wt" checkout --quiet --detach "$REF_SHA" 2>"$job_dir/.checkout.err" || refuse "write side clone checkout failed: $(<"$job_dir/.checkout.err")"
        write_clone_desc="git clone of $RESOLVED_REPO at $REF_SHA"
    else
        mkdir -- "$job_dir/wt" || refuse "cannot create write side clone"
        git -C "$job_dir/wt" init --quiet || refuse "write side clone git init failed"
        rsync -a --exclude='.git' -- "$job_dir/snapshot"/ "$job_dir/wt"/ || refuse "write side clone seed failed"
        git -C "$job_dir/wt" -c user.name="$AGENT_NAME" -c user.email="$AGENT_EMAIL" add -A || refuse "write baseline add failed"
        git -C "$job_dir/wt" -c user.name="$AGENT_NAME" -c user.email="$AGENT_EMAIL" commit --quiet -m "snapshot baseline: $snapshot_source_desc" || refuse "write baseline commit failed"
        REF_SHA=$(git -C "$job_dir/wt" rev-parse HEAD)
        snapshot_source_desc="path $RESOLVED_SCOPE (copied; baseline $REF_SHA)"
        write_clone_desc="git init + baseline commit at $REF_SHA, seeded from $RESOLVED_SCOPE"
    fi
    claude_cwd=$(realpath -e -- "$job_dir/wt") || refuse "write side clone did not resolve"
    exclude_file=$(git -C "$job_dir/wt" rev-parse --git-path info/exclude 2>/dev/null) ||
        refuse "write side clone's per-clone exclude path could not be resolved"
    [[ "$exclude_file" == /* ]] || exclude_file="$job_dir/wt/$exclude_file"
    printf '/.levers/\n' >>"$exclude_file" ||
        refuse "cannot exclude provisioned .levers from the write side clone"
fi
chmod -R a-w -- "$job_dir/snapshot" || refuse "cannot make base snapshot read-only"

if [[ -n "$prompt_file" ]]; then cp -- "$resolved_prompt_file" "$job_dir/prompt.txt" || refuse "cannot copy prompt"; else printf '%s\n' "$prompt_text" >"$job_dir/prompt.txt" || refuse "cannot write prompt"; fi
[[ -s "$job_dir/prompt.txt" ]] || refuse "rendered prompt.txt is empty"

# Invoke the canonical module and verify both status and non-empty output before
# evaluating it. For Claude, ISO_ENV is empty and ISO_FLAGS is the mechanism.
iso_output=$(python3 "$ISOLATION_PY" --sh claude "$job_dir/unused-home" 2>"$job_dir/.isolation.err"); iso_rc=$?
[[ $iso_rc -eq 0 ]] || refuse "isolation.py could not isolate claude (exit $iso_rc): $(<"$job_dir/.isolation.err")"
[[ -n "$iso_output" ]] || refuse "isolation.py produced no output for claude; refusing the eval-empty shape"
eval "$iso_output"
[[ -z "${ISO_ENV:-}" ]] || refuse "claude isolation unexpectedly requested environment overrides '$ISO_ENV'"
[[ "${ISO_FLAGS:-}" == "--setting-sources project,local --strict-mcp-config --disable-slash-commands" ]] || refuse "claude isolation flags differ from the canonical expected boundary: '${ISO_FLAGS:-}'"
iso_extra_flags=(); read -r -a iso_extra_flags <<<"$ISO_FLAGS"

if [[ "$provision_levers" == "yes" ]]; then
    mkdir -- "$claude_cwd/.levers" || refuse "cannot create provisioned .levers directory"
    cp -- "$LEVERS_DIR/grok-dispatch.sh" "$LEVERS_DIR/selftest.sh" "$claude_cwd/.levers/" || refuse "cannot provision sibling levers"
    cp -R -- "$LEVERS_DIR/codex" "$claude_cwd/.levers/codex" || refuse "cannot provision codex lever and vendor"
    [[ ! -e "$claude_cwd/.levers/apply-push.sh" ]] || refuse "apply-push.sh entered provisioned custody unexpectedly"
fi

permission_mode="plan"; [[ "$role" == "write" ]] && permission_mode="acceptEdits"
child_env=("HOME=$OPERATOR_HOME" "PATH=/usr/bin:/bin:/usr/local/bin" "TERM=dumb" "NO_COLOR=1" "LANG=C.UTF-8" "LC_ALL=C.UTF-8" "GIT_TERMINAL_PROMPT=0")
[[ "$record_evidence" == "yes" ]] && child_env+=("WF_AGENT=$AGENT_IDENTITY")
claude_argv=("$CLAUDE_BIN" -p --model "$model" --permission-mode "$permission_mode" --output-format text "${iso_extra_flags[@]}")
started=$(date -u +%Y-%m-%dT%H:%M:%SZ); start_epoch=$(date -u +%s)
(cd -- "$claude_cwd" && exec env -i "${child_env[@]}" timeout --signal=KILL "${wall_timeout}s" "${claude_argv[@]}" <"$job_dir/prompt.txt") >"$job_dir/stdout.log" 2>"$job_dir/stderr.log"
exit_code=$?; ended=$(date -u +%Y-%m-%dT%H:%M:%SZ); end_epoch=$(date -u +%s)
printf '%s\n' "${claude_argv[@]}" >"$job_dir/argv.txt"

commit_attempted="no"; commit_sha=""; commit_message=""; commit_changed=""; commit_note=""
if [[ "$role" == "write" ]]; then
    commit_attempted="yes"
    if ! git -C "$job_dir/wt" add -A 2>"$job_dir/.postadd.err"; then commit_note="git add failed: $(<"$job_dir/.postadd.err")"
    elif git -C "$job_dir/wt" diff --cached --quiet; then commit_note="fable made no file changes; nothing to commit"
    else
        commit_changed=$(git -C "$job_dir/wt" diff --cached --name-only | tr '\n' ' ')
        commit_message="fable write job $job_id"
        if git -C "$job_dir/wt" -c user.name="$AGENT_NAME" -c user.email="$AGENT_EMAIL" commit --quiet -m "$commit_message" 2>"$job_dir/.postcommit.err"; then
            commit_sha=$(git -C "$job_dir/wt" rev-parse HEAD); commit_note="committed by lever after Fable turn"
        else commit_note="commit failed: $(<"$job_dir/.postcommit.err")"; fi
    fi
fi

python3 - "$job_dir" "$job_id" "$SNAPSHOT_MODE" "$snapshot_source_desc" "$role" "$model" "$permission_mode" "$wall_timeout" "$started" "$ended" "$start_epoch" "$end_epoch" "$exit_code" "$record_evidence" "$write_clone_desc" "$claude_cwd" "${ISO_STORE:-}" "$commit_attempted" "$commit_sha" "$commit_message" "$commit_changed" "$commit_note" "$provision_levers" <<'PY'
import json, os, sys
(job, jid, mode, source, role, model, permission, timeout, started, ended,
 start_epoch, end_epoch, rc, evidence, clone_desc, cwd, store, attempted,
 sha, message, changed, note, provisioned) = sys.argv[1:24]
snap = os.path.join(job, "snapshot")
record = {
 "job_id": jid, "harness": "claude", "scope": {"mode": mode, "source": source},
 "prompt_file": "prompt.txt",
 "runtime": {"model": model, "role": role, "permission_mode": permission,
             "wall_timeout_s": int(timeout), "cwd": cwd},
 "isolation": {"mechanism": "flags", "law_source": ".dev/app/harness/isolation.py",
               "flags": ["--setting-sources", "project,local", "--strict-mcp-config", "--disable-slash-commands"],
               "auth_files": [], "operator_home_unchanged": True, "session_store": store,
               "env_allowlisted": True, "record_evidence": evidence == "yes"},
 "snapshot_proof": {"top_level_entries": sorted(os.listdir(snap)),
                    "file_count": sum(len(f) for _,_,f in os.walk(snap)), "read_only_on_disk": True},
 "write_clone": ({"path": os.path.join(job,"wt"), "built_by": clone_desc,
                  "post_run_commit": {"attempted": attempted == "yes", "sha": sha or None,
                  "message": message or None, "changed_files": changed.split() if changed else [], "note": note or None}}
                 if clone_desc else None),
 "provisioned_levers": provisioned == "yes",
 "stamp": {"started": started, "ended": ended, "wall_seconds": int(end_epoch)-int(start_epoch)},
 "exit_code": int(rc), "artifacts": {"stdout":"stdout.log","stderr":"stderr.log","argv":"argv.txt"}}
with open(os.path.join(job,"run-record.json"),"w",encoding="utf-8") as fh:
 json.dump(record,fh,indent=2); fh.write("\n")
PY
printf '%s: job=%s exit=%s dir=%s\n' "$PROGRAM" "$job_id" "$exit_code" "$job_dir"
exit "$exit_code"
