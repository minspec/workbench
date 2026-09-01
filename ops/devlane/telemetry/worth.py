#!/usr/bin/env python3
"""worth — costs and results, joined (ops/process/worth.md).

`report` joins the window's per-harness spend (same stores and
accounting rules as usage.py) with what the repo's history says
landed; `waste` ranks the window's sessions by spend and names the
signals. Every figure is produced at run time and stamped with the
window and repo state it was measured against. Grok cost stays in
raw ticks: the scale is unverified, so no figure here is ever USD.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

CHURN_FACTOR = 20
MERGE_PR = re.compile(r"^Merge pull request #(\d+)\b")
_CLAUDE_CURRENCIES = frozenset((
    "input_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "output_tokens"))
_CODEX_CURRENCIES = frozenset((
    "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_output_tokens", "total_tokens"))
_GROK_CURRENCIES = frozenset((
    "inputTokens", "cachedReadTokens", "cacheCreationTokens",
    "outputTokens", "totalTokens", "reasoningTokens", "costUsdTicks"))


def parse_stamp(value):
    """Store-side: a malformed timestamp raises ValueError so the
    reader can treat the SESSION as unparseable — a gap, never a
    crash and never a silently dropped row."""
    if not isinstance(value, str):
        # ValueError on purpose: callers treat every malformed stamp
        # as one class of gap, whatever the malformation
        raise ValueError(f"non-string timestamp: {value!r}")
    trimmed = re.sub(r"(\.\d{6})\d+", r"\1", value)
    stamp = datetime.fromisoformat(trimmed)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def parse_iso(value, label):
    try:
        return parse_stamp(value)
    except ValueError:
        raise SystemExit2(
            f"invalid {label}: {value!r} is not ISO 8601") from None


class SystemExit2(Exception):
    pass


# ---------------------------------------------------------------- stores

def read_jsonl(path):
    """Every line parses or the file is unparseable — a half-read
    store silently under-reports, which is worse than a gap."""
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, ValueError):
        return None
    return rows


def claude_sessions(root, repo, window):
    """Per-message accounting, last write per message id wins (the
    measured re-emission double-count), cwd-filtered to the repo."""
    since, until = window
    sessions = {}
    for path in sorted(Path(root).glob("*/*.jsonl")):
        rows = read_jsonl(path)
        if rows is None:
            continue
        in_window = {}
        try:
            for row in rows:
                if row.get("cwd") != repo:
                    continue
                message = row.get("message") or {}
                usage = message.get("usage")
                mid = message.get("id")
                stamp = row.get("timestamp")
                if not (isinstance(usage, dict) and mid and stamp):
                    continue
                if not set(usage) & _CLAUDE_CURRENCIES:
                    # an empty usage dict is unknown spend, and an
                    # unknown rendered as 0 is a false measurement
                    continue
                at = parse_stamp(stamp)
                # last-wins among IN-WINDOW emissions: a re-emission
                # after the window must not erase history from it
                if since <= at < until:
                    in_window[mid] = (at, stamp, usage)
        except ValueError:
            continue
        if not in_window:
            continue
        sid = path.stem
        totals = {"in": 0, "cached": 0, "out": 0}
        heavy = None
        for mid, (_at, _raw, usage) in sorted(in_window.items()):
            out = usage.get("output_tokens", 0)
            totals["in"] += usage.get("input_tokens", 0)
            totals["cached"] += (usage.get("cache_creation_input_tokens", 0)
                                 + usage.get("cache_read_input_tokens", 0))
            totals["out"] += out
            if heavy is None or out > heavy["out"]:
                heavy = {"message": mid, "out": out}
        cached_read = sum(
            usage.get("cache_read_input_tokens", 0)
            for _, _, usage in in_window.values()
        )
        sessions[sid] = {
            "messages": len(in_window),
            "in": totals["in"],
            "cached": totals["cached"],
            "out": totals["out"],
            "total": totals["in"] + totals["cached"] + totals["out"],
            "cached_read": cached_read,
            "heavy": heavy,
        }
    return sessions


def codex_sessions(root, repo, window):
    """token_count events are cumulative; the last in-window count IS
    the spend (summing them is the measured double-count mistake)."""
    since, until = window
    sessions = {}
    for path in sorted(Path(root).glob("sessions/*/*/*/rollout-*.jsonl")):
        rows = read_jsonl(path)
        if rows is None:
            continue
        meta = next((row for row in rows
                     if row.get("type") == "session_meta"), None)
        if not meta or (meta.get("payload") or {}).get("cwd") != repo:
            continue
        sid = (meta.get("payload") or {}).get("id") or path.stem
        counts = []
        try:
            for row in rows:
                payload = row.get("payload") or {}
                if (row.get("type") == "event_msg"
                        and payload.get("type") == "token_count"):
                    usage = (payload.get("info") or {}).get(
                        "total_token_usage")
                    stamp = row.get("timestamp")
                    if (isinstance(usage, dict) and stamp
                            and set(usage) & _CODEX_CURRENCIES):
                        counts.append((parse_stamp(stamp), stamp, usage))
        except ValueError:
            continue
        in_window = [(at, raw, usage) for at, raw, usage in counts
                     if since <= at < until]
        if not in_window:
            continue
        last = in_window[-1][2]
        # counts are cumulative for the SESSION: the last pre-window
        # count is the baseline, or a straddling session charges its
        # pre-window spend to this window
        baseline = {}
        for at, _raw, usage in counts:
            if at < since:
                baseline = usage

        def net(key, last=last, baseline=baseline):
            return last.get(key, 0) - baseline.get(key, 0)

        # the heaviest MESSAGE is the largest step between
        # consecutive counts, session-wide so the first in-window
        # message is not credited with pre-window spend
        deltas = {}
        prev_out = 0
        for _at, raw, usage in counts:
            out_here = usage.get("output_tokens", 0)
            deltas[raw] = out_here - prev_out
            prev_out = out_here
        _, heavy_raw, heavy_usage = max(
            in_window, key=lambda item: deltas.get(item[1], 0))
        sessions[sid] = {
            "messages": len(in_window),
            "in": net("input_tokens"),
            "cached": net("cached_input_tokens"),
            "out": net("output_tokens"),
            "total": net("input_tokens") + net("output_tokens"),
            "cached_read": net("cached_input_tokens"),
            "heavy": {"at": heavy_raw,
                      "out": heavy_usage.get("output_tokens", 0),
                      "out_delta": deltas.get(heavy_raw, 0)},
        }
    return sessions


def grok_runs(rows):
    """usage.py's run accounting: cumulative within a run, a run ends
    when a REPORTED totalTokens shrinks, absent totals merge, the last
    report per currency wins."""
    runs = []
    current = {}
    current_at = None
    prev_total = None
    run_incomplete = False
    for row in rows:
        update = (row.get("params") or {}).get("update")
        if not (isinstance(update, dict)
                and update.get("sessionUpdate") == "turn_completed"):
            continue
        usage = update.get("usage")
        if not isinstance(usage, dict) or not set(usage) & _GROK_CURRENCIES:
            continue
        total = usage.get("totalTokens")
        if (total is not None and prev_total is not None
                and total < prev_total):
            if current:
                runs.append((current, current_at, run_incomplete))
            current = {}
            run_incomplete = False
        if total is not None:
            prev_total = total
        current.update(usage)
        if usage.get("usageIsIncomplete"):
            run_incomplete = True
        current_at = row.get("timestamp")
    if current:
        runs.append((current, current_at, run_incomplete))
    return runs


def _summary_overlaps(summary, since, until):
    try:
        created = parse_stamp(summary.get("created_at"))
        updated = parse_stamp(summary.get("updated_at"))
    except ValueError:
        return False
    return created < until and updated >= since


def grok_sessions(root, repo, window):
    since, until = window
    sessions = {}
    base = Path(root) / "sessions" / quote(repo, safe="")
    for session_dir in sorted(base.iterdir()) if base.is_dir() else []:
        try:
            summary = json.loads(
                (session_dir / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # not even attributable to this repo
        if (summary.get("info") or {}).get("cwd") != repo:
            continue
        sid = session_dir.name
        updates = read_jsonl(session_dir / "updates.jsonl")
        if updates is None:
            # attributable session whose spend is unreadable: it MUST
            # hold a place in the counted=N/M denominator, or a store
            # with holes reports itself complete (Codex audit, PR #31)
            if _summary_overlaps(summary, since, until):
                sessions[sid] = {"runs": 0, "incomplete": False}
            continue
        runs = grok_runs(updates)
        if any(at is not None and not isinstance(at, (int, float))
               for _, at, _ in runs):
            # a non-numeric run timestamp is a malformed store shape
            # (events.jsonl uses ISO; updates.jsonl is epoch) — the
            # session's spend is unreadable, not zero and not a crash
            if _summary_overlaps(summary, since, until):
                sessions[sid] = {"runs": 0, "incomplete": False}
            continue
        in_window = []
        incomplete = False
        for usage, at_epoch, run_incomplete in runs:
            if at_epoch is None:
                continue
            at = datetime.fromtimestamp(at_epoch, timezone.utc)
            if since <= at < until:
                in_window.append(usage)
                # incompleteness rides the runs INSIDE the window: an
                # incomplete run elsewhere says nothing about these
                if run_incomplete:
                    incomplete = True
        if runs and not in_window:
            continue  # usage exists, none of it in this window
        if not runs:
            present = any(
                since <= datetime.fromtimestamp(row.get("timestamp", 0),
                                                timezone.utc) < until
                for row in updates
                if isinstance(row.get("timestamp"), (int, float))
            )
            if not present:
                continue
        record = {"runs": len(in_window), "incomplete": incomplete}
        if in_window:
            record["in"] = sum(u.get("inputTokens", 0) for u in in_window)
            record["cached"] = sum(u.get("cachedReadTokens", 0)
                                   + u.get("cacheCreationTokens", 0)
                                   for u in in_window)
            record["out"] = sum(u.get("outputTokens", 0) for u in in_window)
            record["total"] = sum(u.get("totalTokens", 0) for u in in_window)
            record["cached_read"] = sum(u.get("cachedReadTokens", 0)
                                        for u in in_window)
            if all("costUsdTicks" in u for u in in_window):
                record["ticks"] = sum(u["costUsdTicks"] for u in in_window)
            heaviest = max(in_window,
                           key=lambda u: u.get("outputTokens", 0))
            record["heavy"] = {"out": heaviest.get("outputTokens", 0)}
        sessions[sid] = record
    return sessions


# ---------------------------------------------------------------- git

def run_git(repo, *args):
    proc = subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def repo_results(repo, window):
    since, until = window
    stdout = run_git(repo, "log", "--first-parent",
                     "--format=%H%x00%cI%x00%s%x00%P", "HEAD")
    line_log = []
    for line in (stdout or "").splitlines():
        parts = line.split("\x00")
        if len(parts) == 4:
            sha, cdate, subject, parents = parts
            line_log.append((sha, parse_iso(cdate, "commit date"),
                             subject, parents.split()))
    merges, prs, commits = [], [], 0
    for sha, at, subject, parents in line_log:
        if not since <= at < until:
            continue
        if len(parents) > 1:
            entry = {"sha": sha[:7], "subject": subject}
            numbered = MERGE_PR.match(subject)
            if numbered:
                entry["pr"] = int(numbered.group(1))
                prs.append(int(numbered.group(1)))
            merges.append(entry)
        else:
            commits += 1

    def edge_sha(predicate):
        for sha, at, _, _ in line_log:
            if predicate(at):
                return sha
        return None

    def count_tests(sha):
        # git grep -I skips binary blobs, so an image in the tree is
        # not a decode crash; exit 1 just means zero definitions
        proc = subprocess.run(
            ["git", "-C", repo, "grep", "-I", "-c", "-E",
             r"^[[:space:]]*(#\[test\][[:space:]]*$|def test_)", sha],
            capture_output=True, text=True, check=False)
        if proc.returncode == 1:
            return 0
        if proc.returncode != 0:
            return None
        return sum(int(line.rsplit(":", 1)[1])
                   for line in proc.stdout.splitlines() if ":" in line)

    since_sha = edge_sha(lambda at: at <= since)
    until_sha = edge_sha(lambda at: at < until)
    tests = {}
    since_count = count_tests(since_sha) if since_sha else None
    until_count = count_tests(until_sha) if until_sha else None
    tests["since"] = "unrecorded" if since_count is None else since_count
    tests["until"] = "unrecorded" if until_count is None else until_count
    if since_count is None or until_count is None:
        tests["delta"] = "unrecorded"
    else:
        tests["delta"] = until_count - since_count
    merges.reverse()  # oldest first, the order they landed
    return {"commits": commits, "prs": prs, "merges": merges,
            "tests": tests}


# ---------------------------------------------------------------- output

def cost_record(harness, sessions):
    if not sessions:
        return {"sessions": 0, "tokens": "unrecorded"}
    record = {"sessions": len(sessions)}
    if harness == "grok":
        counted = {sid: s for sid, s in sessions.items() if s["runs"]}
        record["runs"] = sum(s["runs"] for s in counted.values())
        # counted=N/M always accompanies a non-empty session list: its
        # absence is how a store with holes passes as complete
        record["counted"] = f"{len(counted)}/{len(sessions)}"
        if counted:
            for key in ("in", "cached", "out", "total"):
                record[key] = sum(s[key] for s in counted.values())
            if all("ticks" in s for s in counted.values()):
                record["cost_usd_ticks"] = sum(
                    s["ticks"] for s in counted.values())
            else:
                record["cost_usd_ticks"] = "unrecorded"
            record["incomplete"] = any(
                s["incomplete"] for s in counted.values())
        else:
            record["tokens"] = "unrecorded"
    else:
        record["messages"] = sum(s["messages"] for s in sessions.values())
        for key in ("in", "cached", "out", "total"):
            record[key] = sum(s[key] for s in sessions.values())
    return record


def plain_cost_line(harness, record):
    parts = [f"{harness:7s}"]
    for key in ("sessions", "messages", "runs", "in", "cached", "out",
                "total", "counted", "cost_usd_ticks", "tokens"):
        if key in record:
            parts.append(f"{key}={record[key]}")
    if record.get("incomplete"):
        parts.append("(incomplete)")
    return " ".join(parts)


def stamp_block(repo, window, now, harness_sessions=None):
    since, until = window
    head = (run_git(repo, "rev-parse", "--short", "HEAD") or "").strip()
    branch = (run_git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    stamp = {"head": head or "unrecorded", "branch": branch or "unrecorded",
             "since": since, "until": until, "now": now}
    if harness_sessions is not None:
        stamp["sessions"] = harness_sessions
    return stamp


def plain_stamp(stamp):
    line = (f"stamp head={stamp['head']} branch={stamp['branch']} "
            f"window=[{stamp['since']}, {stamp['until']}) "
            f"now={stamp['now']}")
    return line


def gather(args, window):
    repo = str(args.repo)
    return {
        "claude": claude_sessions(args.claude_dir, repo, window),
        "codex": codex_sessions(args.codex_dir, repo, window),
        "grok": grok_sessions(args.grok_dir, repo, window),
    }


def cmd_report(args, window, window_iso):
    per_harness = gather(args, window)
    cost = {harness: cost_record(harness, sessions)
            for harness, sessions in per_harness.items()}
    results = repo_results(str(args.repo), window)
    stamp = stamp_block(str(args.repo), window_iso, args.now)
    data = {"stamp": stamp, "cost": cost, "results": results}
    if args.format == "json":
        print(json.dumps(data, sort_keys=True))
        return
    lines = [plain_stamp(stamp), ""]
    for harness in ("claude", "codex", "grok"):
        lines.append(plain_cost_line(harness, cost[harness]))
    lines.append("")
    prs = ",".join(str(n) for n in results["prs"]) or "0"
    lines.append(f"results commits={results['commits']} prs={prs}")
    for merge in results["merges"]:
        label = f"#{merge['pr']} " if "pr" in merge else ""
        lines.append(f"merge {label}{merge['sha']} {merge['subject']}")
    tests = results["tests"]
    lines.append(f"tests delta={tests['delta']} since={tests['since']}"
                 f" until={tests['until']}")
    print("\n".join(lines))


def cmd_waste(args, window, window_iso):
    per_harness = gather(args, window)
    ranked = []
    for harness, sessions in per_harness.items():
        for sid, s in sessions.items():
            if "total" not in s:
                continue
            entry = {"harness": harness, "session": sid,
                     "total": s["total"], "out": s["out"],
                     "cached": s["cached"]}
            if harness == "grok":
                entry["runs"] = s["runs"]
            else:
                entry["messages"] = s["messages"]
            ranked.append((s, entry))
    ranked.sort(key=lambda item: (-item[1]["total"], item[1]["session"]))
    ranked = ranked[:args.top]

    signals = []
    for s, entry in ranked:
        if s["out"] > 0 and s["cached_read"] > CHURN_FACTOR * s["out"]:
            signals.append({"kind": "cache-churn",
                            "harness": entry["harness"],
                            "session": entry["session"],
                            "cached_read": s["cached_read"],
                            "out": s["out"]})
    for s, entry in ranked:
        heavy = s.get("heavy")
        if heavy:
            signal = {"kind": "heavy-turn", "harness": entry["harness"],
                      "session": entry["session"]}
            signal.update(heavy)
            signals.append(signal)

    counts = {harness: len(sessions)
              for harness, sessions in per_harness.items()}
    stamp = stamp_block(str(args.repo), window_iso, args.now,
                        harness_sessions=counts)
    data = {"stamp": stamp, "sessions": [entry for _, entry in ranked],
            "signals": signals}
    if args.format == "json":
        print(json.dumps(data, sort_keys=True))
        return
    lines = [plain_stamp(stamp), ""]
    for harness in ("claude", "codex", "grok"):
        lines.append(f"{harness:7s} sessions={counts[harness]}")
    lines.append("")
    for _, entry in ranked:
        parts = [f"{entry['harness']:7s} session={entry['session']}"]
        for key in ("total", "out", "cached", "messages", "runs"):
            if key in entry:
                parts.append(f"{key}={entry[key]}")
        lines.append(" ".join(parts))
    for signal in signals:
        parts = [(f"signal {signal['kind']} harness={signal['harness']}"
                  f" session={signal['session']}")]
        for key, value in signal.items():
            if key not in ("kind", "harness", "session"):
                parts.append(f"{key}={value}")
        lines.append(" ".join(parts))
    print("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="worth.py", description=__doc__)
    parser.add_argument("verb", choices=("report", "waste"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--now")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--format", choices=("plain", "json"),
                        default="plain")
    parser.add_argument("--claude-dir",
                        default=str(Path.home() / ".claude" / "projects"))
    parser.add_argument("--codex-dir", default=str(Path.home() / ".codex"))
    parser.add_argument("--grok-dir", default=str(Path.home() / ".grok"))
    args = parser.parse_args(argv)

    try:
        if args.now is None:
            now = datetime.now(timezone.utc)
            args.now = now.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z")
        else:
            now = parse_iso(args.now, "now")
        until = parse_iso(args.until, "until") if args.until else now
        since = (parse_iso(args.since, "since") if args.since
                 else now - timedelta(hours=24))
        if until <= since:
            raise SystemExit2(
                f"until ({args.until or args.now}) must be after"
                f" since ({args.since})")
    except SystemExit2 as exc:
        print(str(exc), file=sys.stderr)
        return 2

    since_iso = args.since or since.isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    until_iso = args.until or args.now
    window = (since, until)
    window_iso = (since_iso, until_iso)
    if args.verb == "report":
        cmd_report(args, window, window_iso)
    else:
        cmd_waste(args, window, window_iso)
    return 0


if __name__ == "__main__":
    sys.exit(main())
