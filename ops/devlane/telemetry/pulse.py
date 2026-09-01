#!/usr/bin/env python3
"""Live status of running harness sessions, from their own streams.

    pulse.py [--json] [--live-window S] [--now TS] [--repo PATH]
             [--tail N] [--claude-dir D] [--codex-dir D] [--grok-dir D]

usage.py answers after the fact and breaker.py trips on failure; pulse
answers "what is running right now, and what is it doing" — the question
this repo kept assembling by hand from ls/tail/cat. One compact line per
live session: identity, age, idle time, spend so far, and the recent
tool/event NAMES. Never content: no prompt text, no tool inputs, no
results leave the stores through this tool.

Time is a variable the caller controls: ``--now`` injects the clock and
makes the output a pure function of the stores; the real clock is read
in exactly one place, only when --now is absent. A session is live when
its stream file changed within --live-window seconds of now (boundary
inclusive). Grok sessions with turn_completed usage report the token
dict; pre-upgrade sessions without usage events say
``tokens=unrecorded`` — a stated gap, never a zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

DEFAULT_WINDOW = 300
DEFAULT_TAIL = 5


def _tail(items, tail):
    # recent[-0:] would be the WHOLE history; --tail 0 must mean none.
    return items[-tail:] if tail > 0 else []


def _epoch(stamp: str) -> float:
    text = stamp.strip().replace("Z", "+00:00")
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)
    from datetime import datetime

    return datetime.fromisoformat(text).timestamp()


def _read_jsonl(path: Path):
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except ValueError:
            continue


def _native_epoch(value) -> float:
    """Live stores mix formats: Grok updates stamp epoch integers while
    its events stamp ISO strings. Normalize both for one merge order."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return _epoch(str(value))
    except ValueError:
        return 0.0


def _grok_usage_totals(events):
    """Mirror of usage.py's accounting (measured 2026-08-21): usage is
    cumulative within a run, runs split when a REPORTED totalTokens
    shrinks, and the four token currencies' last-reported values per
    run are summed across runs (reasoning and cost deliberately stay
    out of pulse's closed row)."""
    runs, current, prev_total = [], {}, None
    for usage in events:
        if "totalTokens" not in usage:
            for key in ("inputTokens", "outputTokens", "totalTokens",
                        "cachedReadTokens", "cacheCreationTokens"):
                if key in usage:
                    current[key] = usage[key]
            continue
        total = usage["totalTokens"]
        if prev_total is not None and total < prev_total:
            # Runs split on a cumulative shrink, never on numTurns —
            # see usage.py's accounting note (skeptic-measured).
            runs.append(current)
            current = {}
        prev_total = total
        for key in ("inputTokens", "outputTokens", "totalTokens",
                    "cachedReadTokens", "cacheCreationTokens"):
            if key in usage:
                current[key] = usage[key]
    runs.append(current)
    totals = {key: sum(run.get(key, 0) for run in runs)
              for key in ("inputTokens", "outputTokens", "totalTokens",
                          "cachedReadTokens", "cacheCreationTokens")}
    return {"input": totals["inputTokens"],
            "cached": (totals["cachedReadTokens"]
                       + totals["cacheCreationTokens"]),
            "output": totals["outputTokens"],
            "total": totals["totalTokens"]}


def _idle(path: Path, now: float) -> float:
    """Fractional idle: the liveness comparison happens BEFORE integer
    presentation truncation, so an mtime 300.8s old is dead for a 300s
    window even though it prints as idle=300s."""
    return now - path.stat().st_mtime


def claude_rows(root: Path, repo, now, window, tail):
    if not root.is_dir():
        return
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        if repo:
            slug = "-" + "-".join(repo.strip("/").split("/"))
            if project.name != slug:
                continue
        for stream in sorted(project.glob("*.jsonl")):
            idle = _idle(stream, now)
            if idle > window:
                continue
            started = model = None
            per_msg = {}
            recent = []
            cwds = set()
            for entry in _read_jsonl(stream):
                if entry.get("cwd"):
                    cwds.add(entry["cwd"])
                stamp = entry.get("timestamp")
                if stamp and started is None:
                    started = _epoch(stamp)
                message = entry.get("message") or {}
                usage = message.get("usage")
                mid = message.get("id")
                if usage and mid:
                    model = message.get("model") or model
                    # Keyed by id: a re-emitted message must not
                    # double-count its spend.
                    per_msg[mid] = {
                        "input": usage.get("input_tokens") or 0,
                        "cached": (usage.get("cache_creation_input_tokens") or 0)
                        + (usage.get("cache_read_input_tokens") or 0),
                        "output": usage.get("output_tokens") or 0,
                    }
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        recent.append(str(block.get("name")))
            if repo and repo not in cwds:
                # Two paths can flatten to one slug; the cwd stored in the
                # entries is the truth the directory name is not.
                continue
            tokens = None
            if per_msg:
                tokens = {key: sum(m[key] for m in per_msg.values())
                          for key in ("input", "cached", "output")}
                tokens["total"] = sum(tokens.values())
            yield {"harness": "claude", "session": stream.stem,
                   "model": model,
                   "age_seconds": int(now - started) if started else None,
                   "idle_seconds": int(idle), "tokens": tokens,
                   "recent": _tail(recent, tail)}


def codex_rows(root: Path, repo, now, window, tail):
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for stream in sorted(sessions.glob("*/*/*/rollout-*.jsonl")):
        idle = _idle(stream, now)
        if idle > window:
            continue
        meta, last_count, started, recent = {}, None, None, []
        for entry in _read_jsonl(stream):
            stamp = entry.get("timestamp")
            if stamp and started is None:
                started = _epoch(stamp)
            payload = entry.get("payload") or {}
            if "cwd" in payload:
                # Measured split: session_meta carries id/cwd, turn_context
                # carries model/effort. Merge, never replace.
                meta = {**meta, **payload}
            kind = payload.get("name") or payload.get("type")
            if kind:
                recent.append(str(kind))
            if kind == "token_count":
                info = payload.get("info") or {}
                last_count = info.get("total_token_usage") or last_count
        if repo and meta.get("cwd") != repo:
            continue
        tokens = None
        if last_count:
            tokens = {"input": last_count.get("input_tokens", 0),
                      "cached": last_count.get("cached_input_tokens", 0),
                      "output": last_count.get("output_tokens", 0),
                      "total": last_count.get("total_tokens", 0)}
        yield {"harness": "codex",
               "session": meta.get("id", stream.stem), "model": meta.get("model"),
               "age_seconds": int(now - started) if started else None,
               "idle_seconds": int(idle), "tokens": tokens,
               "recent": _tail(recent, tail)}


def grok_rows(root: Path, repo, now, window, tail):
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for cwd_dir in sorted(p for p in sessions.iterdir() if p.is_dir()):
        if repo and cwd_dir.name != quote(repo, safe=""):
            continue
        for sdir in sorted(p for p in cwd_dir.iterdir() if p.is_dir()):
            streams = [sdir / name for name in ("updates.jsonl", "events.jsonl")
                       if (sdir / name).is_file()]
            if not streams:
                continue
            idle = min(_idle(s, now) for s in streams)
            if idle > window:
                continue
            summary = {}
            summary_path = sdir / "summary.json"
            if summary_path.is_file():
                try:
                    summary = json.loads(summary_path.read_text())
                except ValueError:
                    summary = {}
            started = None
            if summary.get("created_at"):
                started = _epoch(summary["created_at"])
            activity = []
            usage_events = []
            updates_path = sdir / "updates.jsonl"
            updates = (list(_read_jsonl(updates_path))
                       if updates_path.is_file() else [])
            for entry in updates:
                update = (entry.get("params") or {}).get("update") or {}
                if update.get("sessionUpdate") == "turn_completed":
                    # Spend records, not activity: they must not pollute
                    # the recent names.
                    if isinstance(update.get("usage"), dict):
                        usage_events.append(update["usage"])
                    continue
                if entry.get("method"):
                    activity.append((_native_epoch(entry.get("timestamp")),
                                     str(entry["method"])))
            events_path = sdir / "events.jsonl"
            for entry in (_read_jsonl(events_path)
                          if events_path.is_file() else ()):
                name = entry.get("tool_name") or entry.get("type")
                if name:
                    activity.append((_native_epoch(entry.get("ts")),
                                     str(name)))
            activity.sort(key=lambda pair: pair[0])
            yield {"harness": "grok",
                   "session": (summary.get("info") or {}).get("id", sdir.name),
                   "model": summary.get("current_model_id"),
                   "age_seconds": int(now - started) if started else None,
                   "idle_seconds": int(idle),
                   "tokens": (_grok_usage_totals(usage_events)
                              if usage_events else None),
                   "recent": _tail([name for _, name in activity], tail)}


def collect(args, now):
    rows = []
    rows += list(claude_rows(Path(args.claude_dir), args.repo, now,
                             args.live_window, args.tail))
    rows += list(codex_rows(Path(args.codex_dir), args.repo, now,
                            args.live_window, args.tail))
    rows += list(grok_rows(Path(args.grok_dir), args.repo, now,
                           args.live_window, args.tail))
    rows.sort(key=lambda row: (row["harness"], str(row["session"])))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-window", dest="live_window", type=int,
                        default=DEFAULT_WINDOW)
    parser.add_argument("--now", type=float, default=None,
                        help="injected clock (epoch seconds); the real "
                             "clock is read only when absent")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--tail", type=int, default=DEFAULT_TAIL)
    home = Path.home()
    parser.add_argument("--claude-dir", default=str(home / ".claude" / "projects"))
    parser.add_argument("--codex-dir", default=None,
                        help="default: $CODEX_HOME if set, else ~/.codex —"
                             " resolved per invocation, never at import")
    parser.add_argument("--grok-dir", default=str(home / ".grok"))
    args = parser.parse_args(argv)
    if args.codex_dir is None:
        args.codex_dir = os.environ.get("CODEX_HOME") or str(home / ".codex")

    now = args.now if args.now is not None else time.time()
    rows = collect(args, now)
    if args.json:
        print(json.dumps({"sessions": rows}, sort_keys=True))
        return 0
    if not rows:
        print("no live sessions")
        return 0
    for row in rows:
        tokens = row["tokens"]
        spend = f"tokens={tokens['total']}" if tokens else "tokens=unrecorded"
        print(f"{row['harness']:<7} {row['session']} {row['model']}"
              f" age={row['age_seconds']}s idle={row['idle_seconds']}s"
              f" {spend} recent={','.join(row['recent'])}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
