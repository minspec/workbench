#!/usr/bin/env bash
# grok-dispatch.sh — custody-isolated headless Grok dispatch lever.
#
# Launches ONE headless, single-turn Grok investigation job against an
# isolated snapshot (a git ref archived read-only out of a repo) or an
# explicit path subtree copied into a per-job directory. Never runs
# against the operator's $HOME, never inherits the operator's shell
# environment, never touches the live repository working tree.
#
# Every precondition below is an explicit check. If isolation cannot be
# established, this script REFUSES (exit 64) before Grok is ever
# invoked — it does not fall back to an unisolated run, and it does not
# treat "no output" as success.
#
# Usage:
#   grok-dispatch.sh --scope-ref REF --repo PATH   (snapshot of repo at ref)
#   grok-dispatch.sh --scope-path DIR              (explicit subtree, copied)
#   ... plus one of:
#   --prompt TEXT | --prompt-file PATH
#   ... and optionally:
#   --model MODEL --output-format FMT --permission-mode MODE
#   --max-turns N --timeout SECONDS --allow-web-search
#   --jobs-root DIR --record-evidence
#
# Job output lands in: <jobs-root>/<job-id>/
#   snapshot/        the isolated copy Grok actually saw, nothing else
#   prompt.txt       the exact prompt bytes handed to Grok
#   home/            isolated GROK_HOME=HOME, containing only auth.json
#   stdout.log       Grok's stdout (the plain/json response)
#   stderr.log       Grok's stderr
#   run-record.json  job id, argv, isolation proof, timestamps, exit code

set -u -o pipefail

readonly PROGRAM=${0##*/}
LEVERS_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) ||
    { printf '%s: refused: cannot resolve script directory\n' "$PROGRAM" >&2; exit 64; }
readonly LEVERS_DIR
readonly JOBS_ROOT_DEFAULT="$LEVERS_DIR/jobs"

refuse() {
    printf '%s: REFUSED: %s\n' "$PROGRAM" "$*" >&2
    exit 64
}

usage() {
    cat <<'EOF'
grok-dispatch.sh --scope-ref REF --repo PATH | --scope-path DIR
                  (--prompt TEXT | --prompt-file PATH)
                  [--model MODEL] [--output-format FMT]
                  [--permission-mode MODE] [--max-turns N]
                  [--timeout SECONDS] [--allow-web-search]
                  [--jobs-root DIR] [--record-evidence]
EOF
}

# ---- defaults -------------------------------------------------------
scope_ref=""
scope_repo=""
scope_path=""
prompt_text=""
prompt_file=""
model=""
output_format="plain"
# NOT "plan": measured live (2026-08-27) that under --permission-mode plan
# a headless run's run_terminal_command tool call comes back "User
# cancelled the execution" -- plan mode asks for approval on shell
# execution and headless has no one to answer, so it soft-denies and the
# job still exits 0 having done nothing. "auto" auto-approves tool use
# inside the already-isolated, disposable snapshot/home, which is where
# this lever's containment actually lives (see CUSTODY notes in the
# header) rather than in the permission gate.
permission_mode="auto"
max_turns="8"
wall_timeout="300"
allow_web_search="no"
jobs_root="$JOBS_ROOT_DEFAULT"
record_evidence="no"
readonly AGENT_IDENTITY="Grok Investigator <noreply@grok-lever>"

# ---- args -------------------------------------------------------------
while (( $# )); do
    case "$1" in
        --scope-ref)        scope_ref=${2:-};        shift 2 ;;
        --repo)             scope_repo=${2:-};        shift 2 ;;
        --scope-path)       scope_path=${2:-};        shift 2 ;;
        --prompt)           prompt_text=${2:-};       shift 2 ;;
        --prompt-file)      prompt_file=${2:-};       shift 2 ;;
        --model)            model=${2:-};             shift 2 ;;
        --output-format)    output_format=${2:-};     shift 2 ;;
        --permission-mode)  permission_mode=${2:-};   shift 2 ;;
        --max-turns)        max_turns=${2:-};         shift 2 ;;
        --timeout)          wall_timeout=${2:-};      shift 2 ;;
        --allow-web-search) allow_web_search="yes";   shift ;;
        --jobs-root)        jobs_root=${2:-};         shift 2 ;;
        --record-evidence)  record_evidence="yes";    shift ;;
        -h|--help)          usage; exit 0 ;;
        *) refuse "unknown argument '$1'" ;;
    esac
done

# ---- preconditions: scope -------------------------------------------
[[ -n "${HOME:-}" ]] ||
    refuse "HOME is unset or empty; the operator/isolation boundary cannot be resolved"
readonly OPERATOR_HOME=$HOME

if [[ -n "$scope_ref" && -n "$scope_path" ]]; then
    refuse "give exactly one of --scope-ref or --scope-path, not both"
fi
if [[ -z "$scope_ref" && -z "$scope_path" ]]; then
    refuse "no scope given: pass --scope-ref REF --repo PATH, or --scope-path DIR — there is no default scope, and the default is never the live repo or \$HOME"
fi

if [[ -n "$scope_ref" ]]; then
    [[ -n "$scope_repo" ]] ||
        refuse "--scope-ref requires --repo PATH naming the repository to archive it from"
    resolved_repo=$(realpath -e -- "$scope_repo" 2>/dev/null) ||
        refuse "--repo '$scope_repo' does not resolve to an existing path"
    git -C "$resolved_repo" rev-parse --git-dir >/dev/null 2>&1 ||
        refuse "'$resolved_repo' is not a git repository"
    ref_sha=$(git -C "$resolved_repo" rev-parse --verify --end-of-options \
        "${scope_ref}^{commit}" 2>&1) ||
        refuse "--scope-ref '$scope_ref' does not resolve to a commit in '$resolved_repo': $ref_sha"
    readonly SNAPSHOT_MODE="ref"
    readonly RESOLVED_REPO=$resolved_repo
    readonly REF_SHA=$ref_sha
else
    resolved_scope=$(realpath -e -- "$scope_path" 2>/dev/null) ||
        refuse "--scope-path '$scope_path' does not resolve to an existing path"
    [[ -d "$resolved_scope" ]] ||
        refuse "--scope-path '$resolved_scope' is not a directory"
    resolved_home=$(realpath -e -- "$OPERATOR_HOME" 2>/dev/null) || resolved_home=$OPERATOR_HOME
    if [[ "$resolved_scope" == "$resolved_home" ]]; then
        refuse "--scope-path resolves to the operator's \$HOME ($resolved_home); this is never permitted, no override exists"
    fi
    if [[ "$resolved_home" == "$resolved_scope"/* ]]; then
        refuse "--scope-path '$resolved_scope' is an ancestor of the operator's \$HOME; this is never permitted"
    fi
    for guard in .ssh .aws .gnupg .grok .claude .codex .config; do
        if [[ "$resolved_scope" == "$resolved_home/$guard" || "$resolved_scope" == "$resolved_home/$guard"/* ]]; then
            refuse "--scope-path '$resolved_scope' is inside the operator's '$guard' directory; refusing"
        fi
    done
    readonly SNAPSHOT_MODE="path"
    readonly RESOLVED_SCOPE=$resolved_scope
fi

# ---- preconditions: prompt --------------------------------------------
if [[ -n "$prompt_text" && -n "$prompt_file" ]]; then
    refuse "give exactly one of --prompt or --prompt-file, not both"
fi
if [[ -z "$prompt_text" && -z "$prompt_file" ]]; then
    refuse "no prompt given: pass --prompt TEXT or --prompt-file PATH"
fi
if [[ -n "$prompt_file" ]]; then
    resolved_prompt_file=$(realpath -e -- "$prompt_file" 2>/dev/null) ||
        refuse "--prompt-file '$prompt_file' does not resolve to an existing file"
    [[ -f "$resolved_prompt_file" && -s "$resolved_prompt_file" ]] ||
        refuse "--prompt-file '$resolved_prompt_file' is not a non-empty regular file"
fi
if [[ -n "$prompt_text" ]]; then
    # A prompt that is only whitespace is the "eval \"\"" shape: it would
    # exit clean having asked Grok nothing. Refuse it explicitly.
    trimmed=${prompt_text//[$'\t\r\n ']/}
    [[ -n "$trimmed" ]] ||
        refuse "--prompt is empty or whitespace-only; refusing rather than dispatching an empty job"
fi

# ---- preconditions: output/permission dials ---------------------------
case "$output_format" in
    plain|json|streaming-json|streaming-messages-json) ;;
    *) refuse "--output-format '$output_format' is not one of plain|json|streaming-json|streaming-messages-json" ;;
esac
case "$permission_mode" in
    default|acceptEdits|auto|dontAsk|bypassPermissions|plan) ;;
    *) refuse "--permission-mode '$permission_mode' is not one of default|acceptEdits|auto|dontAsk|bypassPermissions|plan" ;;
esac
[[ "$max_turns" =~ ^[0-9]+$ && "$max_turns" -gt 0 ]] ||
    refuse "--max-turns '$max_turns' must be a positive integer"
[[ "$wall_timeout" =~ ^[0-9]+$ && "$wall_timeout" -gt 0 ]] ||
    refuse "--timeout '$wall_timeout' must be a positive integer number of seconds"

# ---- preconditions: the grok binary and its credential -----------------
grok_bin=$(command -v grok 2>/dev/null || true)
if [[ -z "$grok_bin" ]]; then
    for candidate in "$OPERATOR_HOME/.grok/bin/grok" /home/work/.grok/bin/grok; do
        if [[ -x "$candidate" ]]; then grok_bin=$candidate; break; fi
    done
fi
[[ -n "$grok_bin" && -x "$grok_bin" ]] ||
    refuse "no executable 'grok' binary found on PATH or at the known install location"
readonly GROK_BIN=$grok_bin

source_grok_home=${GROK_HOME:-$OPERATOR_HOME/.grok}
source_auth="$source_grok_home/auth.json"
[[ -f "$source_auth" && -r "$source_auth" ]] ||
    refuse "grok credential '$source_auth' is absent or unreadable; refusing an unisolated fallback (this is the BLOCKER case: fix auth, do not disable isolation)"
resolved_auth=$(realpath -e -- "$source_auth" 2>/dev/null) ||
    refuse "grok credential '$source_auth' cannot be resolved"
readonly RESOLVED_AUTH=$resolved_auth

# ---- job directory ------------------------------------------------------
mkdir -p -- "$jobs_root" || refuse "cannot create jobs root '$jobs_root'"
jobs_root=$(realpath -e -- "$jobs_root") || refuse "jobs root '$jobs_root' did not resolve after creation"
job_id="$(date -u +%Y%m%dT%H%M%SZ)-grok-$(od -An -tx1 -N3 /dev/urandom | tr -d ' \n')"
job_dir="$jobs_root/$job_id"
mkdir -- "$job_dir" || refuse "job directory '$job_dir' already exists or could not be created"
mkdir -- "$job_dir/snapshot" "$job_dir/home" || refuse "cannot create job subdirectories under '$job_dir'"
chmod 700 -- "$job_dir/home" || refuse "cannot protect isolated home '$job_dir/home'"

# ---- build the snapshot (read-only from the source; never mutates it) --
if [[ "$SNAPSHOT_MODE" == "ref" ]]; then
    if ! git -C "$RESOLVED_REPO" archive --format=tar "$REF_SHA" 2>"$job_dir/.archive.err" \
            | tar -x -C "$job_dir/snapshot"; then
        refuse "snapshot: 'git archive' of $REF_SHA from '$RESOLVED_REPO' failed: $(cat -- "$job_dir/.archive.err" 2>/dev/null)"
    fi
    rm -f -- "$job_dir/.archive.err"
    snapshot_source_desc="ref $scope_ref ($REF_SHA) archived from $RESOLVED_REPO"
else
    if ! rsync -a --exclude='.git' -- "$RESOLVED_SCOPE"/ "$job_dir/snapshot"/ 2>"$job_dir/.rsync.err"; then
        refuse "snapshot: copying '$RESOLVED_SCOPE' failed: $(cat -- "$job_dir/.rsync.err" 2>/dev/null)"
    fi
    rm -f -- "$job_dir/.rsync.err"
    snapshot_source_desc="path $RESOLVED_SCOPE (copied)"
fi

# FAIL CLOSED: an empty snapshot is exactly the "eval \"\"" shape — it
# would let Grok run, see nothing worth objecting to, exit 0, and be
# reported as a clean job. Refuse before Grok is ever launched.
if [[ -z "$(find "$job_dir/snapshot" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    refuse "snapshot at '$job_dir/snapshot' is empty ($snapshot_source_desc); refusing to dispatch against an empty scope"
fi

# ---- write the prompt, verbatim, inside the job directory --------------
if [[ -n "$prompt_file" ]]; then
    cp -- "$resolved_prompt_file" "$job_dir/prompt.txt" ||
        refuse "cannot copy prompt file into job directory"
else
    printf '%s\n' "$prompt_text" > "$job_dir/prompt.txt" ||
        refuse "cannot write prompt into job directory"
fi
[[ -s "$job_dir/prompt.txt" ]] ||
    refuse "rendered prompt.txt is empty after being written; refusing to dispatch"

# ---- build the isolated home: exactly one file, a symlink to auth -----
ln -s -- "$RESOLVED_AUTH" "$job_dir/home/auth.json" ||
    refuse "cannot link the sole allowed grok credential into the isolated home"
home_entries=$(ls -A -- "$job_dir/home")
[[ "$home_entries" == "auth.json" ]] ||
    refuse "isolated home '$job_dir/home' contains more than auth.json ($home_entries); refusing to launch with an unverified isolation boundary"

# ---- assemble the child's environment: an explicit allowlist, never ----
# ---- the operator's inherited environment.                            --
child_env=(
    "HOME=$job_dir/home"
    "GROK_HOME=$job_dir/home"
    "PATH=/usr/bin:/bin:/usr/local/bin"
    "TERM=dumb"
    "NO_COLOR=1"
    "CLICOLOR=0"
    "LANG=C.UTF-8"
    "LC_ALL=C.UTF-8"
    "GIT_TERMINAL_PROMPT=0"
)
if [[ "$record_evidence" == "yes" ]]; then
    child_env+=("WF_AGENT=$AGENT_IDENTITY")
fi

# ---- assemble Grok's argv ------------------------------------------------
grok_argv=(
    "$GROK_BIN"
    --prompt-file "$job_dir/prompt.txt"
    --output-format "$output_format"
    --permission-mode "$permission_mode"
    --max-turns "$max_turns"
)
[[ "$allow_web_search" == "yes" ]] || grok_argv+=(--disable-web-search)
[[ -n "$model" ]] && grok_argv+=(-m "$model")

# ---- launch, wall-clock capped, cwd pinned to the snapshot -------------
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
start_epoch=$(date -u +%s)
(
    cd -- "$job_dir/snapshot" &&
    exec env -i "${child_env[@]}" timeout --signal=KILL "${wall_timeout}s" "${grok_argv[@]}"
) >"$job_dir/stdout.log" 2>"$job_dir/stderr.log"
exit_code=$?
ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
end_epoch=$(date -u +%s)

printf '%s\n' "${grok_argv[@]}" > "$job_dir/argv.txt"

# ---- write the run record -----------------------------------------------
python3 - "$job_dir" "$job_id" "$SNAPSHOT_MODE" "$snapshot_source_desc" \
    "$permission_mode" "$output_format" "$model" "$max_turns" "$wall_timeout" \
    "$started" "$ended" "$start_epoch" "$end_epoch" "$exit_code" "$record_evidence" <<'PY'
import json, os, sys

(job_dir, job_id, mode, source_desc, permission_mode, output_format, model,
 max_turns, wall_timeout, started, ended, start_epoch, end_epoch, exit_code,
 record_evidence) = sys.argv[1:16]

home_dir = os.path.join(job_dir, "home")
snapshot_dir = os.path.join(job_dir, "snapshot")
snapshot_entries = sorted(os.listdir(snapshot_dir))
file_count = sum(len(files) for _, _, files in os.walk(snapshot_dir))

record = {
    "job_id": job_id,
    "harness": "grok",
    "scope": {"mode": mode, "source": source_desc},
    "prompt_file": "prompt.txt",
    "runtime": {
        "model": model or None,
        "output_format": output_format,
        "permission_mode": permission_mode,
        "max_turns": int(max_turns),
        "wall_timeout_s": int(wall_timeout),
        "web_search_disabled": True,
    },
    "isolation": {
        "home": home_dir,
        "home_contents": sorted(os.listdir(home_dir)),
        "env_allowlisted": True,
        "record_evidence": record_evidence == "yes",
    },
    "snapshot_proof": {
        "top_level_entries": snapshot_entries,
        "file_count": file_count,
    },
    "stamp": {
        "started": started,
        "ended": ended,
        "wall_seconds": int(end_epoch) - int(start_epoch),
    },
    "exit_code": int(exit_code),
    "artifacts": {
        "stdout": "stdout.log",
        "stderr": "stderr.log",
        "argv": "argv.txt",
    },
}
with open(os.path.join(job_dir, "run-record.json"), "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2)
    fh.write("\n")
PY

printf '%s: job=%s exit=%s dir=%s\n' "$PROGRAM" "$job_id" "$exit_code" "$job_dir"
exit "$exit_code"
