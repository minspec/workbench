#!/usr/bin/env bash
# context-stream.sh — an append-only record of everything that invalidated context.
#
# A session that starts blind rebuilds state from whatever is visible now, which is how a
# measurement from days ago gets repeated as current. The current state cannot tell you
# what it used to be — so the moments it changed get written down as they happen.
#
#   context-stream.sh record [kind]  append what just happened, plus any state delta
#   context-stream.sh since <ts>   entries newer than an ISO timestamp
#   context-stream.sh tail [n]     the last n entries, readable (default 15)
#
# Stored in the clone's shared git directory, so it is per-CLONE and cannot be committed by
# accident. Its value is recency, not history.
#
# ONE STREAM PER CLONE, and --git-common-dir is what makes that true. CONTRIB.md mandates a
# worktree per line of work; in a linked worktree `git rev-parse --git-dir` answers
# `.git/worktrees/<name>`, so a stream keyed on it forks into one private history per tree
# while the git-native recorders next door (post-commit, post-checkout) keep appending to
# the shared file. Measured: the Claude-side entry landed in .git/worktrees/wt/ and the
# commit entry in .git/, and `tail` showed a different history from each location.
#
# The last-seen SNAPSHOT stays per-worktree, and deliberately: it answers "what did THIS
# tree look like when I last looked here", and each worktree has its own branch and HEAD.
# Sharing it would make the first record from a second worktree report a checkout nobody
# made -- the branch changed because the reader moved, not because the ground did.

set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
common=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
[ -n "$common" ] || exit 0
gitdir=$(git rev-parse --git-dir 2>/dev/null) || exit 0
[ -n "$gitdir" ] || exit 0
# Both answers may be relative to the CURRENT directory, and the next line leaves it.
case "$common" in /*) ;; *) common="$PWD/$common" ;; esac
case "$gitdir" in /*) ;; *) gitdir="$PWD/$gitdir" ;; esac
common=$(cd "$common" 2>/dev/null && pwd) || exit 0
gitdir=$(cd "$gitdir" 2>/dev/null && pwd) || exit 0
cd "$root"
STREAM="$common/claude-context-stream.jsonl"
STATE="$gitdir/claude-context-state.json"

base_ref() {
    for c in upstream/main upstream/master origin/main origin/master main master; do
        git rev-parse --verify -q "$c" >/dev/null 2>&1 && { echo "$c"; return; }
    done
}

record() {
    python3 - "$STREAM" "$STATE" "$(base_ref)" "${1:-}" <<'PY'
import json, os, subprocess, sys, datetime

stream, state_path, base = sys.argv[1], sys.argv[2], sys.argv[3]
kind_hint = sys.argv[4] if len(sys.argv) > 4 else ""

def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:
        return ""

now = {
    "branch": sh("git", "branch", "--show-current") or "?",
    "head":   sh("git", "rev-parse", "--short", "HEAD") or "?",
    "base":   sh("git", "rev-parse", "--short", base) if base else "",
}
prev = {}
if os.path.exists(state_path):
    try: prev = json.load(open(state_path))
    except Exception: prev = {}

events = []
# the base ref moving invalidates the most, so it carries what it touched
if prev.get("base") and now["base"] and prev["base"] != now["base"]:
    rng = f'{prev["base"]}..{now["base"]}'
    n = sh("git", "rev-list", "--count", rng) or "?"
    files = sh("git", "diff", "--name-only", rng).splitlines()[:8]
    events.append(("base", f'{base} {prev["base"]} -> {now["base"]} ({n} commits)', " ".join(files)))

if prev.get("branch") and prev["branch"] != now["branch"]:
    events.append(("branch", f'checkout {prev["branch"]} -> {now["branch"]}', ""))
elif prev.get("head") and prev["head"] != now["head"]:
    events.append(("head", f'{now["branch"]} moved {prev["head"]} -> {now["head"]}', ""))

# Record the crossing itself even when the net state is unchanged. A command can check out
# a branch, build, and return before this hook runs — the delta is zero and the crossing
# still happened, which is exactly what a later session needs to know. Logging only deltas
# left the stream nearly empty across a whole session of real work.
if kind_hint and not events:
    events.append((kind_hint, f'{kind_hint} while on {now["branch"]} at {now["head"]}', ""))

if events:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(stream, "a") as f:
        for kind, what, detail in events:
            f.write(json.dumps({"at": ts, "kind": kind, "what": what, "detail": detail}) + "\n")

json.dump(now, open(state_path, "w"))
PY
}

read_stream() {  # read_stream <mode> <arg>
    [ -f "$STREAM" ] || { [ "$1" = tail ] && echo "  (no context stream yet)"; return 0; }
    python3 - "$STREAM" "$1" "$2" <<'PY'
import json, sys
path, mode, arg = sys.argv[1], sys.argv[2], sys.argv[3]
entries = []
for line in open(path):
    raw = line.rstrip("\n")
    if not raw.strip():
        continue
    try:
        entry = json.loads(raw)
    except Exception:
        # An unreadable line is still a record. Dropping it silently was how a
        # reader could disagree with the file it was reading and say nothing.
        entries.append((raw, None))
        continue
    entries.append((raw, entry))
if mode == "since":
    entries = [p for p in entries if p[1] is None or p[1].get("at", "") > arg]
else:
    entries = entries[-int(arg or 15):]
for raw, e in entries:
    if e is None:
        print(f'  (unreadable) {raw[:160]}')
        continue
    when = e.get("at", "")[5:16].replace("T", " ")
    # tolerate entries written by earlier versions of this tool, which had no "what"
    what = e.get("what") or f'{e.get("kind","?")} on {e.get("branch","?")} at {e.get("head","?")}'
    print(f'  {when}  [{e.get("kind","?"):6}] {what}')
    if e.get("detail"):
        print(f'                     touched: {e["detail"][:96]}')
    # The record itself, verbatim, under its own rendering. The rendering is
    # lossy — it drops the year, the seconds, the agent and the worktree — and a
    # lossy view cannot answer the question this tool now has to answer from
    # every worktree: is this the same stream the other tree is reading? Two
    # readers comparing summaries agree while reading different files.
    print(f'                     record: {raw}')
PY
}

case "${1:-tail}" in
    record) record "${2:-}" ;;
    since)  read_stream since "${2:-1970-01-01T00:00:00Z}" ;;
    tail)   read_stream tail "${2:-15}" ;;
    *) echo "usage: context-stream.sh {record|since <iso-ts>|tail [n]}" >&2; exit 2 ;;
esac
