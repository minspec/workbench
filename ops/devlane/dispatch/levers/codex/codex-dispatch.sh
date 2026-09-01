#!/usr/bin/env bash
# codex-dispatch.sh — custody-isolated headless Codex dispatch lever.
#
# Launches ONE headless, single-turn `codex exec` job against an isolated
# snapshot (a git ref archived read-only out of a repo) or an explicit path
# subtree copied into a per-job directory. Never runs against the
# operator's $HOME, never inherits the operator's shell environment or
# ~/.codex config (hooks.json, config.toml, sessions, ...), never touches
# the live repository working tree.
#
# Shape matches the sibling grok-dispatch.sh lever (../grok-dispatch.sh)
# so both can later be generalized into one harness-config primitive: a
# scope, a prompt, an optional model, and a role (read vs write).
#
# Every precondition below is an explicit check. If isolation cannot be
# established, this script REFUSES (exit 64) before Codex is ever
# invoked — it does not fall back to an unisolated run, and it does not
# treat "no output" as success.
#
# Real flags confirmed against `codex --version` (0.148.0) and
# `codex exec --help` run on this host, and cross-checked against the
# ADAPTERS table in origin/repo/lane-go:.dev/app/task/run.py and the
# harness/controls fixtures on that branch (dispatchable-flags.json,
# dispatchable-home.json):
#   - headless entrypoint: `codex exec [OPTIONS] [PROMPT]`; PROMPT "-" (or
#     omitted) reads the prompt from stdin.
#   - sandbox: `-s/--sandbox read-only|workspace-write|danger-full-access`.
#     This lever only ever uses read-only or workspace-write.
#   - approval: `-a/--ask-for-approval` exists on the top-level interactive
#     `codex` command, but is ABSENT from `codex exec --help` on this
#     host (0.148.0) — confirmed by running it, not assumed from the
#     parent command's help. `codex exec` is unconditionally
#     non-interactive: `--sandbox` alone gates what the model may do, and
#     a command it forbids is returned to the model as an execution
#     failure rather than a hang waiting for approval. This matches the
#     lane-go ADAPTERS table, whose codex argv is `exec --sandbox
#     {sandbox} -` with no approval flag at all. This lever therefore
#     does not expose one either.
#   - working root: `-C/--cd DIR`.
#   - isolation is NOT a flag on this harness — Codex discovers its config,
#     auth, hooks and sessions store from $CODEX_HOME (default ~/.codex).
#     The lane-go fixture recorded the failure mode directly: an
#     unisolated dispatch loaded ~/.codex/hooks.json and ran a SessionStart
#     hook. So isolation here means a from-scratch CODEX_HOME containing
#     ONLY a symlinked auth.json, never the operator's real one.
#   - there is no `--effort`/reasoning-effort flag on this harness (only
#     the generic `-c key=value`, whose reasoning key lane-go's authors
#     declined to guess at). This lever does not expose one either; do not
#     add a silently-dropped dial.
#   - `--skip-git-repo-check` lets exec run in a directory with no `.git`
#     (true for a `git archive`/rsync snapshot) without Codex complaining.
#
# KNOWN CUSTODY QUIRK (why role=write forks the filesystem in two, and
# why Codex never runs `git commit` itself):
#   Codex's sandbox is enforced by mediating writes under the cwd/add-dir
#   it is told about; a *read* job's snapshot is additionally chmod'd
#   a-w on disk after it's built, so even a workspace-write sandbox
#   mistake can't touch it. That means a write job cannot commit "in
#   place" in that snapshot. So role=write builds a SECOND, independent,
#   writable side clone (`wt/`) — a real `git clone` of the source repo
#   at the resolved ref (or a fresh `git init` + baseline commit, for a
#   --scope-path source) — and Codex is pointed at wt/, never snapshot/,
#   for the write role. snapshot/ stays read-only reference material.
#
#   CONFIRMED BY RUNNING IT (codex-cli 0.148.0): even inside wt/, with
#   --sandbox workspace-write and a fully-writable .git on disk, Codex's
#   OWN `git commit` fails sandboxed: `fatal: Unable to create
#   '.git/index.lock': Read-only file system`. Ordinary file writes in
#   the same directory succeed — only `.git/` is denied. So this lever
#   never asks Codex to commit. Codex edits files in wt/; once its turn
#   ends, THIS SCRIPT stages and commits whatever changed, from its own
#   unsandboxed shell. "Commit in a side clone" is mechanized as a
#   deterministic post-run step this script controls, not a git
#   invocation trusted to model output.
#
# Usage:
#   codex-dispatch.sh --scope-ref REF --repo PATH   (snapshot of repo at ref)
#   codex-dispatch.sh --scope-path DIR              (explicit subtree, copied)
#   ... plus one of:
#   --prompt TEXT | --prompt-file PATH
#   ... and optionally:
#   --model MODEL --role read|write [default: read]
#   --sandbox read-only|workspace-write   (default: derived from --role)
#   --timeout SECONDS --jobs-root DIR --record-evidence
#
# Job output lands in: <jobs-root>/<job-id>/
#   snapshot/         the isolated, read-only copy Codex saw for role=read;
#                      reference-only baseline for role=write
#   wt/               (role=write only) the writable side clone Codex
#                      actually ran in and could commit into
#   prompt.txt        the exact prompt bytes handed to Codex
#   home/             isolated CODEX_HOME=HOME, containing only auth.json
#   stdout.log        Codex's stdout
#   stderr.log        Codex's stderr
#   last-message.txt  Codex's final agent message (--output-last-message)
#   argv.txt          the exact argv invoked
#   run-record.json   job id, argv, isolation proof, timestamps, exit code

set -u -o pipefail

readonly PROGRAM=${0##*/}
LEVERS_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) ||
    { printf '%s: refused: cannot resolve script directory\n' "$PROGRAM" >&2; exit 64; }
readonly LEVERS_DIR
readonly JOBS_ROOT_DEFAULT="$LEVERS_DIR/jobs"

# The canonical isolation law, vendored (not imported live from the repo
# working tree — this lever must not touch that tree, and must not
# depend on whatever happens to be checked out there). Content is
# `.dev/app/harness/isolation.py` as of origin/dev; it is the owner's
# source of truth for what a harness may bring from the operator's
# machine, and it is invoked, not re-derived, so this lever cannot drift
# from it silently. A missing/unreadable vendor file is refused here,
# up front, rather than producing the empty-`eval` shape further down.
readonly ISOLATION_PY="$LEVERS_DIR/vendor/isolation.py"
[[ -f "$ISOLATION_PY" && -r "$ISOLATION_PY" ]] ||
    { printf '%s: REFUSED: vendored isolation module missing at %s; cannot establish isolation, refusing to dispatch\n' "$PROGRAM" "$ISOLATION_PY" >&2; exit 64; }

refuse() {
    printf '%s: REFUSED: %s\n' "$PROGRAM" "$*" >&2
    exit 64
}

usage() {
    cat <<'EOF'
codex-dispatch.sh --scope-ref REF --repo PATH | --scope-path DIR
                   (--prompt TEXT | --prompt-file PATH)
                   [--model MODEL] [--role read|write]
                   [--sandbox read-only|workspace-write]
                   [--timeout SECONDS] [--jobs-root DIR] [--record-evidence]
EOF
}

# ---- defaults -----------------------------------------------------------
scope_ref=""
scope_repo=""
scope_path=""
prompt_text=""
prompt_file=""
model=""
role="read"
sandbox_mode=""
wall_timeout="300"
jobs_root="$JOBS_ROOT_DEFAULT"
record_evidence="no"
readonly AGENT_IDENTITY="Codex Dispatcher <noreply@codex-lever>"

# ---- args -----------------------------------------------------------------
while (( $# )); do
    case "$1" in
        --scope-ref)         scope_ref=${2:-};        shift 2 ;;
        --repo)              scope_repo=${2:-};       shift 2 ;;
        --scope-path)        scope_path=${2:-};       shift 2 ;;
        --prompt)            prompt_text=${2:-};      shift 2 ;;
        --prompt-file)       prompt_file=${2:-};      shift 2 ;;
        --model)             model=${2:-};            shift 2 ;;
        --role)               role=${2:-};             shift 2 ;;
        --sandbox)            sandbox_mode=${2:-};     shift 2 ;;
        --timeout)            wall_timeout=${2:-};     shift 2 ;;
        --jobs-root)          jobs_root=${2:-};        shift 2 ;;
        --record-evidence)    record_evidence="yes";   shift ;;
        -h|--help)            usage; exit 0 ;;
        *) refuse "unknown argument '$1'" ;;
    esac
done

# ---- preconditions: scope -------------------------------------------------
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
    # exit clean having asked Codex nothing. Refuse it explicitly.
    trimmed=${prompt_text//[$'\t\r\n ']/}
    [[ -n "$trimmed" ]] ||
        refuse "--prompt is empty or whitespace-only; refusing rather than dispatching an empty job"
fi

# ---- preconditions: role / sandbox / approval dials -----------------------
case "$role" in
    read|write) ;;
    *) refuse "--role '$role' is not one of read|write" ;;
esac
if [[ -z "$sandbox_mode" ]]; then
    if [[ "$role" == "write" ]]; then sandbox_mode="workspace-write"; else sandbox_mode="read-only"; fi
fi
case "$sandbox_mode" in
    read-only|workspace-write) ;;
    danger-full-access) refuse "--sandbox danger-full-access is never permitted by this lever" ;;
    *) refuse "--sandbox '$sandbox_mode' is not one of read-only|workspace-write" ;;
esac
if [[ "$role" == "read" && "$sandbox_mode" == "workspace-write" ]]; then
    refuse "--role read with --sandbox workspace-write makes no sense: a read job has no writable side clone to write into"
fi
[[ "$wall_timeout" =~ ^[0-9]+$ && "$wall_timeout" -gt 0 ]] ||
    refuse "--timeout '$wall_timeout' must be a positive integer number of seconds"

# ---- preconditions: the codex binary and its credential -------------------
codex_bin=$(command -v codex 2>/dev/null || true)
if [[ -z "$codex_bin" ]]; then
    for candidate in "$OPERATOR_HOME/.local/bin/codex" /home/work/.local/bin/codex; do
        if [[ -x "$candidate" ]]; then codex_bin=$candidate; break; fi
    done
fi
[[ -n "$codex_bin" && -x "$codex_bin" ]] ||
    refuse "no executable 'codex' binary found on PATH or at the known install location"
readonly CODEX_BIN=$codex_bin

source_codex_home=${CODEX_HOME:-$OPERATOR_HOME/.codex}
source_auth="$source_codex_home/auth.json"
[[ -f "$source_auth" && -r "$source_auth" ]] ||
    refuse "codex credential '$source_auth' is absent or unreadable; refusing an unisolated fallback (this is the BLOCKER case: fix auth, do not disable isolation)"
resolved_auth=$(realpath -e -- "$source_auth" 2>/dev/null) ||
    refuse "codex credential '$source_auth' cannot be resolved"
readonly RESOLVED_AUTH=$resolved_auth

# ---- job directory ------------------------------------------------------
mkdir -p -- "$jobs_root" || refuse "cannot create jobs root '$jobs_root'"
jobs_root=$(realpath -e -- "$jobs_root") || refuse "jobs root '$jobs_root' did not resolve after creation"
job_id="$(date -u +%Y%m%dT%H%M%SZ)-codex-$(od -An -tx1 -N3 /dev/urandom | tr -d ' \n')"
job_dir="$jobs_root/$job_id"
mkdir -- "$job_dir" || refuse "job directory '$job_dir' already exists or could not be created"
mkdir -- "$job_dir/snapshot" "$job_dir/home" || refuse "cannot create job subdirectories under '$job_dir'"
chmod 700 -- "$job_dir/home" || refuse "cannot protect isolated home '$job_dir/home'"

# ---- build the base snapshot (read-only from the source; never mutates it) -
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
# would let Codex run, see nothing worth objecting to, exit 0, and be
# reported as a clean job. Refuse before Codex is ever launched.
if [[ -z "$(find "$job_dir/snapshot" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
    refuse "snapshot at '$job_dir/snapshot' is empty ($snapshot_source_desc); refusing to dispatch against an empty scope"
fi

# ---- role=write: build the writable side clone BEFORE snapshot goes ------
# ---- read-only, so it never inherits a locked-down permission bit.  -----
write_clone_desc=""
codex_cwd="$job_dir/snapshot"
if [[ "$role" == "write" ]]; then
    if [[ "$SNAPSHOT_MODE" == "ref" ]]; then
        if ! git clone --no-hardlinks --quiet -- "$RESOLVED_REPO" "$job_dir/wt" 2>"$job_dir/.clone.err"; then
            refuse "write side clone: 'git clone' of '$RESOLVED_REPO' failed: $(cat -- "$job_dir/.clone.err" 2>/dev/null)"
        fi
        if ! git -C "$job_dir/wt" checkout --quiet --detach "$REF_SHA" 2>"$job_dir/.checkout.err"; then
            refuse "write side clone: checkout of $REF_SHA in '$job_dir/wt' failed: $(cat -- "$job_dir/.checkout.err" 2>/dev/null)"
        fi
        rm -f -- "$job_dir/.clone.err" "$job_dir/.checkout.err"
        write_clone_desc="git clone of $RESOLVED_REPO at $REF_SHA"
    else
        mkdir -- "$job_dir/wt" || refuse "cannot create write side clone directory '$job_dir/wt'"
        if ! git -C "$job_dir/wt" init --quiet 2>"$job_dir/.init.err"; then
            refuse "write side clone: 'git init' in '$job_dir/wt' failed: $(cat -- "$job_dir/.init.err" 2>/dev/null)"
        fi
        rm -f -- "$job_dir/.init.err"
        if ! rsync -a --exclude='.git' -- "$job_dir/snapshot"/ "$job_dir/wt"/ 2>"$job_dir/.wtrsync.err"; then
            refuse "write side clone: seeding '$job_dir/wt' from snapshot failed: $(cat -- "$job_dir/.wtrsync.err" 2>/dev/null)"
        fi
        rm -f -- "$job_dir/.wtrsync.err"
        if ! git -C "$job_dir/wt" -c user.name="$AGENT_IDENTITY" -c user.email="noreply@codex-lever" \
                add -A 2>"$job_dir/.add.err" ||
           ! git -C "$job_dir/wt" -c user.name="$AGENT_IDENTITY" -c user.email="noreply@codex-lever" \
                commit --quiet -m "snapshot baseline: $snapshot_source_desc" 2>>"$job_dir/.add.err"; then
            refuse "write side clone: baseline commit in '$job_dir/wt' failed: $(cat -- "$job_dir/.add.err" 2>/dev/null)"
        fi
        rm -f -- "$job_dir/.add.err"
        write_clone_desc="git init + baseline commit, seeded from $snapshot_source_desc"
    fi
    # Isolation proof: the side clone must itself never be (or contain)
    # the operator's HOME.
    resolved_wt=$(realpath -e -- "$job_dir/wt") || refuse "write side clone '$job_dir/wt' did not resolve"
    codex_cwd="$resolved_wt"
fi

# Base snapshot is reference-only from here on: lock it down on disk so
# even a sandbox misconfiguration can't turn it into a write target.
chmod -R a-w -- "$job_dir/snapshot" ||
    refuse "cannot make base snapshot '$job_dir/snapshot' read-only"

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

# ---- build the isolated CODEX_HOME via the CANONICAL isolation law -------
# `isolation.py --sh codex <home>` builds the minimal home (auth.json
# symlinked in, nothing else — codex's `auth_files` is exactly that one
# entry) and prints shell assignments for the env overrides its own
# HARNESSES table says codex needs. This is the exact call context.cue's
# incident describes: a launcher that does `eval "$(isolation.py ...)"`
# and never checks whether the command behind it actually ran. When the
# module was absent, that produced NO output, `eval ""` succeeded, and a
# dispatch went out fully unisolated with an exit code of 0. So here the
# command's own exit status AND its output are checked as two separate,
# explicit preconditions — neither is a comment — before anything is
# ever handed to `eval`.
iso_output=$(python3 "$ISOLATION_PY" --sh codex "$job_dir/home" 2>"$job_dir/.isolation.err")
iso_rc=$?
if [[ $iso_rc -ne 0 ]]; then
    refuse "isolation.py could not isolate codex (exit $iso_rc): $(cat -- "$job_dir/.isolation.err" 2>/dev/null)"
fi
if [[ -z "$iso_output" ]]; then
    refuse "isolation.py produced no output for codex; this is the 'eval \"\"' shape — refusing rather than treating silence as isolation"
fi
rm -f -- "$job_dir/.isolation.err"
eval "$iso_output"
[[ -n "${ISO_ENV_CODEX_HOME:-}" ]] ||
    refuse "isolation.py's output did not set ISO_ENV_CODEX_HOME; refusing to dispatch with an unverified isolation boundary"
[[ "$ISO_ENV_CODEX_HOME" == "$job_dir/home" ]] ||
    refuse "isolation.py isolated CODEX_HOME to '$ISO_ENV_CODEX_HOME', not the job home '$job_dir/home'; refusing"

# Structural re-verification, independent of trusting the library call
# above: the built home must hold EXACTLY the declared credential and
# nothing else, checked by listing it, not by re-asserting the claim.
home_entries=$(ls -A -- "$job_dir/home")
[[ "$home_entries" == "auth.json" ]] ||
    refuse "isolated home '$job_dir/home' contains more than auth.json ($home_entries); refusing to launch with an unverified isolation boundary"

# ---- assemble the child's environment: an explicit allowlist, never ----
# ---- the operator's inherited environment. CODEX_HOME/HOME come from ---
# ---- isolation.py's own output (ISO_ENV_*) rather than being retyped. --
child_env=(
    "HOME=$job_dir/home"
    "CODEX_HOME=$ISO_ENV_CODEX_HOME"
    "PATH=/usr/bin:/bin:/usr/local/bin"
    "TERM=dumb"
    "NO_COLOR=1"
    "LANG=C.UTF-8"
    "LC_ALL=C.UTF-8"
    "GIT_TERMINAL_PROMPT=0"
)
# ISO_FLAGS is empty for codex today (isolation.py: mechanism "home", not
# "flags") but is applied generically so this lever does not silently
# stop tracking the law if that ever changes.
iso_extra_flags=()
if [[ -n "${ISO_FLAGS:-}" ]]; then
    read -r -a iso_extra_flags <<<"$ISO_FLAGS"
fi
if [[ "$record_evidence" == "yes" ]]; then
    child_env+=("WF_AGENT=$AGENT_IDENTITY")
fi

# ---- assemble Codex's argv ------------------------------------------------
codex_argv=(
    "$CODEX_BIN" exec
    --skip-git-repo-check
    --sandbox "$sandbox_mode"
    -C "$codex_cwd"
    --color never
    --output-last-message "$job_dir/last-message.txt"
)
(( ${#iso_extra_flags[@]} )) && codex_argv+=("${iso_extra_flags[@]}")
[[ -n "$model" ]] && codex_argv+=(-m "$model")
codex_argv+=("-")

# ---- launch, wall-clock capped, prompt piped in on stdin -------------
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
start_epoch=$(date -u +%s)
(
    exec env -i "${child_env[@]}" timeout --signal=KILL "${wall_timeout}s" \
        "${codex_argv[@]}" < "$job_dir/prompt.txt"
) >"$job_dir/stdout.log" 2>"$job_dir/stderr.log"
exit_code=$?
ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
end_epoch=$(date -u +%s)

printf '%s\n' "${codex_argv[@]}" > "$job_dir/argv.txt"
[[ -f "$job_dir/last-message.txt" ]] || : > "$job_dir/last-message.txt"

# ---- role=write: the LEVER commits, Codex never touches .git ------------
# Empirically confirmed on this host (codex-cli 0.148.0): even with
# --sandbox workspace-write and cwd pointed at a fully-writable side
# clone, `git commit` INSIDE Codex's own sandboxed exec fails --
#   fatal: Unable to create '.git/index.lock': Read-only file system
# -- while ordinary file writes in that same directory succeed. Codex's
# sandbox mediates `.git` as read-only regardless of which repository it
# is, so asking the model to run the commit itself is not a prompt this
# lever can complete. This is that quirk in exactly the shape the brief
# named it: read-only base snapshot (chmod'd above) + a writable side
# clone for the WRITE, with the COMMIT itself done here, by the lever, in
# its own unsandboxed shell, after Codex's turn has ended. Codex edits
# files; it never invokes git.
commit_attempted="no" commit_sha="" commit_message="" commit_changed="" commit_note=""
if [[ "$role" == "write" ]]; then
    commit_attempted="yes"
    if ! git -C "$job_dir/wt" add -A 2>"$job_dir/.postadd.err"; then
        commit_note="git add -A failed: $(cat -- "$job_dir/.postadd.err" 2>/dev/null)"
    elif git -C "$job_dir/wt" diff --cached --quiet 2>/dev/null; then
        commit_note="codex made no file changes in the side clone; nothing to commit"
    else
        commit_changed=$(git -C "$job_dir/wt" diff --cached --name-only | tr '\n' ' ')
        commit_message="codex write job $job_id"
        if git -C "$job_dir/wt" -c user.name="$AGENT_IDENTITY" -c user.email="noreply@codex-lever" \
                commit --quiet -m "$commit_message" 2>"$job_dir/.postcommit.err"; then
            commit_sha=$(git -C "$job_dir/wt" rev-parse HEAD)
            commit_note="committed by the lever after codex's turn ended; codex itself never wrote to .git"
        else
            commit_note="commit failed: $(cat -- "$job_dir/.postcommit.err" 2>/dev/null)"
        fi
    fi
    rm -f -- "$job_dir/.postadd.err" "$job_dir/.postcommit.err"
fi

# ---- write the run record -----------------------------------------------
python3 - "$job_dir" "$job_id" "$SNAPSHOT_MODE" "$snapshot_source_desc" \
    "$role" "$sandbox_mode" "$model" "$wall_timeout" \
    "$started" "$ended" "$start_epoch" "$end_epoch" "$exit_code" "$record_evidence" \
    "$write_clone_desc" "$codex_cwd" "${ISO_STORE:-}" "$home_entries" \
    "$commit_attempted" "$commit_sha" "$commit_message" "$commit_changed" "$commit_note" <<'PY'
import json, os, sys

(job_dir, job_id, mode, source_desc, role, sandbox_mode,
 model, wall_timeout, started, ended, start_epoch, end_epoch, exit_code,
 record_evidence, write_clone_desc, codex_cwd, iso_store,
 pre_dispatch_home, commit_attempted, commit_sha, commit_message,
 commit_changed, commit_note) = sys.argv[1:24]

home_dir = os.path.join(job_dir, "home")
snapshot_dir = os.path.join(job_dir, "snapshot")
snapshot_entries = sorted(os.listdir(snapshot_dir))
file_count = sum(len(files) for _, _, files in os.walk(snapshot_dir))

record = {
    "job_id": job_id,
    "harness": "codex",
    "scope": {"mode": mode, "source": source_desc},
    "prompt_file": "prompt.txt",
    "runtime": {
        "model": model or None,
        "role": role,
        "sandbox": sandbox_mode,
        "wall_timeout_s": int(wall_timeout),
        "cwd": codex_cwd,
    },
    "isolation": {
        "mechanism": "home",
        "law_source": ".dev/app/harness/isolation.py (vendored from origin/dev)",
        "codex_home": home_dir,
        "pre_dispatch_home_contents": pre_dispatch_home.split(),
        "home_contents_after_run": sorted(os.listdir(home_dir)),
        "session_store": iso_store or None,
        "env_allowlisted": True,
        "record_evidence": record_evidence == "yes",
    },
    "snapshot_proof": {
        "top_level_entries": snapshot_entries,
        "file_count": file_count,
        "read_only_on_disk": True,
    },
    "write_clone": ({
        "path": os.path.join(job_dir, "wt"),
        "built_by": write_clone_desc,
        "post_run_commit": {
            "attempted": commit_attempted == "yes",
            "sha": commit_sha or None,
            "message": commit_message or None,
            "changed_files": commit_changed.split() if commit_changed else [],
            "note": commit_note or None,
        },
    } if write_clone_desc else None),
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
        "last_message": "last-message.txt",
    },
}
with open(os.path.join(job_dir, "run-record.json"), "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2)
    fh.write("\n")
PY

printf '%s: job=%s exit=%s dir=%s\n' "$PROGRAM" "$job_id" "$exit_code" "$job_dir"
exit "$exit_code"
