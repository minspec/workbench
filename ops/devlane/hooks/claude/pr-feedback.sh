#!/usr/bin/env bash
# pr-feedback.sh <pr> [repo] [--watch] — every surface a PR can carry feedback on.
#
# Written because a poller watched `.comments` for a Codex reply, Codex answered on a diff
# line instead, and the poller reported "no response" for forty minutes while the review sat
# there. The check could not observe the thing it was watching for.
#
# Then it happened AGAIN, in the other direction: a hand-rolled loop watched reviews, inline
# comments and reactions, and Codex's clean verdict arrived as a conversation comment. The
# tool that reads all four already existed; what it lacked was a way to WAIT, so a loop got
# written from scratch and re-introduced the bug the tool had fixed. Hence --watch.
#
# Feedback on a pull request lives in FIVE places, and they are different API objects:
#
#   1. issue comments      the conversation tab             /issues/N/comments
#   2. reviews             approve / request-changes bodies /pulls/N/reviews
#   3. review comments     inline, anchored to a diff line  /pulls/N/comments
#   4. reactions           a bot signalling "nothing found" /issues/comments/ID/reactions
#   5. unresolved threads  what the review is still waiting on   GraphQL reviewThreads
#
# Checking a subset and reporting "nothing" is worse than not checking, because it answers
# the question wrongly rather than not answering it.
#
# The endpoints are defined ONCE below and used by both the report and the watch
# fingerprint. Two lists would drift, and the drift would be exactly this bug. Threads were
# in the report and not in the fingerprint, which is that drift: --watch could not notice
# the one surface that says what a review is still blocked on.
#
# A SURFACE THAT COULD NOT BE READ IS NOT AN EMPTY SURFACE. Every `gh` call's status is
# checked; a failed one prints `(UNREADABLE — gh exit N: <first stderr line>)` where the
# listing would have gone, and the run exits 3 — distinct from --watch's 1 for a quiet
# timeout and from 2 for a usage error. This is the incident that produced the tool, made
# by the tool: an auth failure and a genuinely empty PR used to print the same `(none)` and
# the same exit 0, so a network blip read as "the reviewer's comment vanished".
#
# In --watch, a fingerprint taken through a failed call is not a fingerprint. It is never
# compared, the error is said once, polling continues, and the run cannot exit 0 on it.

set -uo pipefail
PR=""; REPO=""; WATCH=0; INTERVAL=20; TIMEOUT=900
while [ $# -gt 0 ]; do
    case "$1" in
        --watch)    WATCH=1 ;;
        --interval) INTERVAL=$2; shift ;;
        --timeout)  TIMEOUT=$2; shift ;;
        -*)         echo "pr-feedback: unknown flag $1" >&2; exit 2 ;;
        *)          if [ -z "$PR" ]; then PR=$1; else REPO=$1; fi ;;
    esac
    shift
done
[ -n "$PR" ] || { echo "usage: pr-feedback.sh <pr> [owner/repo] [--watch] [--interval N] [--timeout N]" >&2; exit 2; }
[ -n "$REPO" ] || REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)
[ -n "$REPO" ] || { echo "pr-feedback: could not determine the repo" >&2; exit 1; }

EXIT_UNREADABLE=3
THREAD_PAGE=50          # the GraphQL bound; paging is below, and it is the point

# ---- the five surfaces, defined once -------------------------------------------------
# per_page=100: the API defaults to 30 per page, and a bare listing silently truncates
# there — which once turned 9 fresh review findings into a reported "clean" pass. 100 is
# the API maximum; cap_warn() below refuses to stay quiet if a surface hits it.
EP_CONV="repos/$REPO/issues/$PR/comments?per_page=100"
EP_REVIEWS="repos/$REPO/pulls/$PR/reviews?per_page=100"
EP_INLINE="repos/$REPO/pulls/$PR/comments?per_page=100"
ep_reactions() { echo "repos/$REPO/issues/comments/$1/reactions?per_page=100"; }

ERRFILE=$(mktemp "${TMPDIR:-/tmp}/pr-feedback-stderr.XXXXXX") || exit 1
trap 'rm -f "$ERRFILE"' EXIT

UNREAD=0                # any call failed since the last reset
API_OUT=""; API_RC=0; API_ERR=""

api() {  # api <gh api args...> — sets API_OUT / API_RC / API_ERR, returns the gh status
    : >"$ERRFILE"
    API_OUT=$(gh api "$@" 2>"$ERRFILE")
    API_RC=$?
    API_ERR=$(head -n 1 "$ERRFILE" 2>/dev/null | tr -d '\000-\037')
    [ "$API_RC" = 0 ] || UNREAD=1
    return "$API_RC"
}

jqr() {  # jqr <json> <filter>
    printf '%s' "$1" | jq -r "$2" 2>/dev/null
}

unreadable() {  # unreadable <rc> <first stderr line>
    if [ -n "$2" ]; then
        printf '    (UNREADABLE — gh exit %s: %s)\n' "$1" "$2"
    else
        # An empty stderr is still not an empty PR. Saying "(none)" here is the
        # whole defect, so the marker stands on the exit status alone.
        printf '    (UNREADABLE — gh exit %s: no stderr)\n' "$1"
    fi
}

cap_warn() {  # cap_warn <endpoint> <json>
    local n
    n=$(jqr "$2" 'if type == "array" then length else 0 end')
    case "$n" in ''|*[!0-9]*) n=0 ;; esac
    [ "$n" -ge 100 ] && printf '    WARNING: %s returned %s items — page cap hit, output may be TRUNCATED\n' "$1" "$n"
    return 0
}

# ---- unresolved threads, paged -------------------------------------------------------
# GraphQL demands a bound, so `first:` is not optional and cannot be raised out of the
# problem: `first:100` is `first:50` with a bigger number in it, and the 101st thread is
# gone the same way. .claude/skills/pr-overview/pr_overview.py already learned this and
# pages; this follows it. Sets THREADS_OUT / THREADS_RC / THREADS_ERR.
threads_query() {  # threads_query [cursor]
    local after=""
    [ -n "${1:-}" ] && after=", after: \"$1\""
    printf '{repository(owner:"%s",name:"%s"){pullRequest(number:%s){reviewThreads(first:%s%s){pageInfo{hasNextPage endCursor} nodes{isResolved path line}}}}}' \
        "${REPO%%/*}" "${REPO##*/}" "$PR" "$THREAD_PAGE" "$after"
}

threads_fetch() {
    local cursor="" page nodes has next pages=0
    THREADS_OUT=""; THREADS_RC=0; THREADS_ERR=""
    while [ "$pages" -lt 200 ]; do
        pages=$((pages + 1))
        if ! api graphql -f query="$(threads_query "$cursor")"; then
            THREADS_RC=$API_RC; THREADS_ERR=$API_ERR
            return 1
        fi
        page=$API_OUT
        nodes=$(jqr "$page" '.data.repository.pullRequest.reviewThreads.nodes[]? | select(.isResolved == false) | "    \(.path):\(.line)"')
        [ -n "$nodes" ] && THREADS_OUT="${THREADS_OUT}${nodes}"$'\n'
        has=$(jqr "$page" '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage // false')
        next=$(jqr "$page" '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor // ""')
        # A page that claims a next page without a cursor to reach it would spin
        # forever on the same first page; so would a cursor that does not move.
        [ "$has" = "true" ] && [ -n "$next" ] && [ "$next" != "null" ] \
            && [ "$next" != "$cursor" ] || break
        cursor=$next
    done
    return 0
}

# A single string that changes when ANY surface changes. Counts alone miss an edit, so the
# last id, the reaction contents and the unresolved threads go in too. Sets FP and FP_OK;
# FP_OK=0 means at least one call failed and FP is not a reading of anything.
fingerprint() {
    local c="" r="" i="" x="" t="" conv=""
    FP_OK=1
    if api "$EP_CONV"; then
        conv=$API_OUT
        c=$(jqr "$conv" '[.[] | "\(.id):\(.updated_at)"] | join(",")')
    else
        FP_OK=0
    fi
    if api "$EP_REVIEWS"; then
        r=$(jqr "$API_OUT" '[.[] | "\(.id):\(.submitted_at // "")"] | join(",")')
    else
        FP_OK=0
    fi
    if api "$EP_INLINE"; then
        i=$(jqr "$API_OUT" '[.[] | "\(.id):\(.updated_at)"] | join(",")')
    else
        FP_OK=0
    fi
    if [ "$FP_OK" = 1 ]; then
        for id in $(jqr "$conv" '.[].id'); do
            if api "$(ep_reactions "$id")"; then
                x="$x$(jqr "$API_OUT" '[.[].content] | join("+")')|"
            else
                FP_OK=0
            fi
        done
    fi
    if threads_fetch; then
        t=$(printf '%s' "$THREADS_OUT" | tr '\n' ',')
    else
        FP_OK=0
    fi
    FP=$(printf 'conv=%s\nrev=%s\ninline=%s\nreact=%s\nthreads=%s\n' "$c" "$r" "$i" "$x" "$t")
}

report() {
    local conv conv_rc conv_err body found failed frc ferr r
    printf '\n%s #%s\n\n' "$REPO" "$PR"

    api "$EP_CONV"; conv=$API_OUT; conv_rc=$API_RC; conv_err=$API_ERR
    printf '  conversation comments\n'
    if [ "$conv_rc" != 0 ]; then
        unreadable "$conv_rc" "$conv_err"
    else
        body=$(jqr "$conv" '.[] | "    \(.created_at[11:16])  \(.user.login): \(.body[0:100] | gsub("\n";" "))"')
        if [ -n "$body" ]; then printf '%s\n' "$body"; else printf '    (none)\n'; fi
        cap_warn "$EP_CONV" "$conv"
    fi

    printf '\n  reviews\n'
    if api "$EP_REVIEWS"; then
        body=$(jqr "$API_OUT" '.[] | "    \(.submitted_at[11:16])  \(.user.login) \(.state): \((.body // "")[0:90] | gsub("\n";" "))"')
        if [ -n "$body" ]; then printf '%s\n' "$body"; else printf '    (none)\n'; fi
        cap_warn "$EP_REVIEWS" "$API_OUT"
    else
        unreadable "$API_RC" "$API_ERR"
    fi

    printf '\n  inline review comments\n'
    if api "$EP_INLINE"; then
        body=$(jqr "$API_OUT" '.[] | "    \(.created_at[11:16])  \(.user.login)  \(.path):\(.line // .original_line)\n      \(.body[0:220] | gsub("\n";" "))"')
        if [ -n "$body" ]; then printf '%s\n' "$body"; else printf '    (none)\n'; fi
        cap_warn "$EP_INLINE" "$API_OUT"
    else
        unreadable "$API_RC" "$API_ERR"
    fi

    # Reaction semantics matter: an automated reviewer reacts 👀 (eyes) when it PICKS THE JOB
    # UP and 👍 (+1) when it finishes having found nothing. Treating any reaction as
    # completion reports a pass while the review is still running.
    printf '\n  reactions on comments  (eyes = picked up, +1 = finished, found nothing)\n'
    if [ "$conv_rc" != 0 ]; then
        # The comment ids come from the conversation listing. Without it there is
        # nothing to enumerate, and "no reactions" would be a guess.
        unreadable "$conv_rc" "$conv_err"
    else
        found=0; failed=0; frc=0; ferr=""
        for id in $(jqr "$conv" '.[].id'); do
            if api "$(ep_reactions "$id")"; then
                r=$(jqr "$API_OUT" '.[] | "    comment '"$id"': \(.content) by \(.user.login)"')
                [ -n "$r" ] && { printf '%s\n' "$r"; found=1; }
            else
                failed=1; frc=$API_RC; ferr=$API_ERR
            fi
        done
        [ "$failed" = 1 ] && unreadable "$frc" "$ferr"
        [ "$found" = 0 ] && [ "$failed" = 0 ] && printf '    (none)\n'
    fi

    printf '\n  unresolved threads\n'
    if threads_fetch; then
        if [ -n "$THREADS_OUT" ]; then printf '%s' "$THREADS_OUT"; else printf '    (none)\n'; fi
    else
        unreadable "$THREADS_RC" "$THREADS_ERR"
    fi
    printf '\n'
}

if [ "$WATCH" = 0 ]; then
    report
    [ "$UNREAD" = 0 ] && exit 0
    exit "$EXIT_UNREADABLE"
fi

fingerprint
base=$FP
base_ok=$FP_OK
said=0
printf '\n  watching all five surfaces of %s #%s (every %ss, up to %ss)\n' "$REPO" "$PR" "$INTERVAL" "$TIMEOUT"
if [ "$base_ok" = 0 ]; then
    printf '\n  UNREADABLE — gh failed taking the baseline; polling continues, but nothing can be compared against a reading that never happened\n'
    said=1
fi
elapsed=0
while [ "$elapsed" -lt "$TIMEOUT" ]; do
    sleep "$INTERVAL"; elapsed=$((elapsed + INTERVAL))
    fingerprint
    if [ "$FP_OK" = 0 ]; then
        # Comparing this would report every surface it could not read as changed,
        # or as unchanged, and both are answers to a question nobody asked.
        if [ "$said" = 0 ]; then
            printf '\n  UNREADABLE — gh failed during a poll after %ss; polling continued, this reading is not compared\n' "$elapsed"
            said=1
        fi
        continue
    fi
    if [ "$base_ok" = 0 ]; then
        # First readable poll after an unreadable baseline. It is a baseline, not a
        # change: nothing is known to have moved between a reading and a non-reading.
        base=$FP; base_ok=1
        continue
    fi
    if [ "$FP" != "$base" ]; then
        # name WHICH surface moved: "something changed" sends you looking in the wrong tab
        printf '\n  changed after %ss:\n' "$elapsed"
        diff <(printf '%s\n' "$base") <(printf '%s\n' "$FP") \
            | grep '^>' | cut -d= -f1 | sed 's/^> /    /' | sort -u
        UNREAD=0
        report
        [ "$UNREAD" = 0 ] && exit 0
        exit "$EXIT_UNREADABLE"
    fi
done
if [ "$said" = 1 ]; then
    printf '\n  polled for %ss with at least one surface UNREADABLE — this is not "no change"\n\n' "$TIMEOUT"
    exit "$EXIT_UNREADABLE"
fi
printf '\n  no change on any of the five surfaces after %ss\n\n' "$TIMEOUT"
exit 1
