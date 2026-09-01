#!/usr/bin/env python3
"""Token and session usage across the three harnesses, from their own stores.

    usage.py sessions [--json] [--repo PATH] [--claude-dir D] [--codex-dir D] [--grok-dir D]
    usage.py report   [same flags]

Nothing here instruments anything: every harness already writes a session
store, and this reads them. Measured shapes (2026-08-21):

- Claude: ``<dir>/<project-slug>/<session>.jsonl`` — per-message
  ``message.usage`` (input, cache_creation, cache_read, output). Summed.
- Codex: ``<dir>/sessions/Y/M/D/rollout-*.jsonl`` — a ``session_meta`` head
  carrying ``cwd``, then cumulative ``token_count`` events. The LAST one is
  the session's usage; summing them would multiply it.
- Grok: ``<dir>/sessions/<urlencoded-cwd>/<id>/`` — summary.json holds
  messages/model/timestamps; spend lives in updates.jsonl
  ``turn_completed`` events (cumulative within a run; runs split when
  totalTokens shrinks; last report per currency wins) including raw
  costUsdTicks. Pre-upgrade sessions have no usage events and stay an
  explicit gap; nothing is ever estimated.

The report is aggregates only. The stores hold prompts and transcripts;
none of that content leaves them through this tool.

Two principles taken from loopstrap's telemetry design (TELEMETRY.md,
xormania/loopstrap): the capture rule — an unknown value stays an explicit
gap, never a zero — and the accounting rule — a session's spend is input +
cache_creation + cache_read + output, cached reads counted as the cheap
class they are, not ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

GROK_GAP = "grok usage not yet parsed: updates.jsonl turn_completed"


def _read_jsonl(path: Path):
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except ValueError:
            continue


def claude_sessions(root: Path, repo: str | None):
    if not root.is_dir():
        return
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        if repo:
            # Claude encodes the project path with '-' separators.
            slug = "-" + "-".join(repo.strip("/").split("/"))
            if project.name != slug:
                continue
        for f in sorted(project.glob("*.jsonl")):
            per_msg = {}
            model = None
            first = last = None
            cwds = set()
            for line_no, entry in enumerate(_read_jsonl(f)):
                if entry.get("cwd"):
                    cwds.add(entry["cwd"])
                stamp = entry.get("timestamp")
                if stamp:
                    first = first or stamp
                    last = stamp
                message = entry.get("message") or {}
                usage = message.get("usage")
                if not usage:
                    continue
                model = message.get("model") or model
                # Keyed by message id, LAST wins: live streams are the
                # snapshot-rewrite shape (measured 2026-08-21: 2086
                # usage lines over 1052 unique ids in one session), and
                # summing every line nearly doubles the spend.
                key = message.get("id") or f"line-{line_no}"
                per_msg[key] = {
                    "input": usage.get("input_tokens", 0),
                    "cached": (usage.get("cache_creation_input_tokens", 0)
                               + usage.get("cache_read_input_tokens", 0)),
                    "output": usage.get("output_tokens", 0),
                }
            if not per_msg:
                continue
            if repo and cwds and repo not in cwds:
                # Two paths can flatten to one directory slug; the cwd
                # stored in the entries is the truth.
                continue
            messages = len(per_msg)
            tokens = {key: sum(m[key] for m in per_msg.values())
                      for key in ("input", "cached", "output")}
            tokens["total"] = sum(tokens.values())
            yield {"harness": "claude", "session": f.stem, "model": model,
                   "started": first, "ended": last, "messages": messages,
                   "tokens": tokens}


def codex_sessions(root: Path, repo: str | None):
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for f in sorted(sessions.glob("*/*/*/rollout-*.jsonl")):
        meta, last_count, events = {}, None, 0
        first = last = None
        for entry in _read_jsonl(f):
            stamp = entry.get("timestamp")
            if stamp:
                first = first or stamp
                last = stamp
            payload = entry.get("payload") or {}
            if "cwd" in payload:
                # Measured split: session_meta carries id/cwd, a later
                # turn_context carries model/effort. Merge, never replace —
                # replacing loses the id to whichever payload came last.
                meta = {**meta, **payload}
            if payload.get("type") == "token_count":
                info = payload.get("info") or {}
                last_count = info.get("total_token_usage") or last_count
            events += 1
        if repo and meta.get("cwd") != repo:
            continue
        tokens = None
        if last_count:
            tokens = {"input": last_count.get("input_tokens", 0),
                      "cached": last_count.get("cached_input_tokens", 0),
                      "output": last_count.get("output_tokens", 0),
                      "total": last_count.get("total_tokens", 0)}
        yield {"harness": "codex", "session": meta.get("id", f.stem),
               "model": meta.get("model"), "started": first, "ended": last,
               "messages": events, "tokens": tokens}


GROK_USAGE_CURRENCIES = (
    "inputTokens", "outputTokens", "totalTokens", "cachedReadTokens",
    "cacheCreationTokens", "reasoningTokens", "costUsdTicks",
)


def _grok_usage_totals(events):
    """Session spend from turn_completed events (measured 2026-08-21):
    usage is cumulative WITHIN a run and a run ends when the cumulative
    totalTokens SHRINKS (the only observable reset signal — turns can
    rise or repeat across a reset). The session figure is each
    currency's last-reported value per run, summed across runs.
    Last-wins-across-the-stream undercounts, summing every event
    overcounts, and max-merge overcounts when a reset arrives without
    a turns drop (measured live, skeptic finding). An event omitting a
    currency does not erase the run's earlier report, and a RUN that
    never reports costUsdTicks makes the session's cost a gap (None) —
    never zero, never a partial sum."""
    runs, current, prev_total = [], {}, None
    incomplete = False
    for usage in events:
        if "totalTokens" not in usage:
            # An event that does not report totals cannot signal a
            # reset; coercing absence to zero invents a run boundary.
            if usage.get("usageIsIncomplete"):
                incomplete = True
            for key in GROK_USAGE_CURRENCIES:
                if key in usage:
                    current[key] = usage[key]
            continue
        total = usage["totalTokens"]
        if prev_total is not None and total < prev_total:
            # A cumulative snapshot that SHRINKS is a new run — the only
            # observable reset signal. numTurns is NOT it: a live stream
            # (019fb283…) reset totals 7.5M→1.9M while turns ROSE 12→15,
            # and turns repeat freely within a run.
            runs.append(current)
            current = {}
        prev_total = total
        if usage.get("usageIsIncomplete"):
            incomplete = True
        for key in GROK_USAGE_CURRENCIES:
            if key in usage:
                # Cumulative within the run: the LAST event to report a
                # currency wins; an event omitting one does not erase
                # the run's earlier report.
                current[key] = usage[key]
    runs.append(current)
    totals = {key: sum(run.get(key, 0) for run in runs)
              for key in GROK_USAGE_CURRENCIES}
    cost = (None if any("costUsdTicks" not in run for run in runs)
            else totals["costUsdTicks"])
    tokens = {"input": totals["inputTokens"],
              "cached": (totals["cachedReadTokens"]
                         + totals["cacheCreationTokens"]),
              "output": totals["outputTokens"],
              "total": totals["totalTokens"]}
    return tokens, totals["reasoningTokens"], cost, incomplete


def _grok_usage_events(updates_path):
    if not updates_path.is_file():
        return []
    events = []
    for entry in _read_jsonl(updates_path):
        update = (entry.get("params") or {}).get("update") or {}
        # Only turn_completed carries spend; usage keys on other update
        # kinds are not accounting records (measured decoy: a tool
        # update carrying a usage dict).
        if update.get("sessionUpdate") == "turn_completed" and isinstance(
            update.get("usage"), dict
        ):
            events.append(update["usage"])
    return events


def grok_sessions(root: Path, repo: str | None):
    sessions = root / "sessions"
    if not sessions.is_dir():
        return
    for cwd_dir in sorted(p for p in sessions.iterdir() if p.is_dir()):
        if repo and cwd_dir.name != quote(repo, safe=""):
            continue
        for sdir in sorted(p for p in cwd_dir.iterdir() if p.is_dir()):
            summary = sdir / "summary.json"
            if not summary.exists():
                continue
            try:
                s = json.loads(summary.read_text())
            except ValueError:
                continue
            row = {"harness": "grok", "incomplete": False,
                   "session": (s.get("info") or {}).get("id", sdir.name),
                   "model": s.get("current_model_id"),
                   "started": s.get("created_at"),
                   "ended": s.get("updated_at"),
                   "messages": s.get("num_messages"),
                   "tokens": None, "reasoning": None,
                   "cost_usd_ticks": None, "note": GROK_GAP}
            events = _grok_usage_events(sdir / "updates.jsonl")
            if events:
                tokens, reasoning, cost, incomplete = _grok_usage_totals(events)
                row["tokens"] = tokens
                row["reasoning"] = reasoning
                row["cost_usd_ticks"] = cost
                row["incomplete"] = incomplete
                row["note"] = ("grok usage parsed from updates.jsonl"
                               " (usageIsIncomplete: figures incomplete)"
                               if incomplete
                               else "grok usage parsed from updates.jsonl")
            yield row


def collect(args):
    rows = []
    rows += list(claude_sessions(Path(args.claude_dir), args.repo))
    rows += list(codex_sessions(Path(args.codex_dir), args.repo))
    rows += list(grok_sessions(Path(args.grok_dir), args.repo))
    rows.sort(key=lambda r: (r.get("started") or "", r["harness"]))
    return rows


def cmd_sessions(args) -> int:
    rows = collect(args)
    if args.json:
        print(json.dumps({"sessions": rows}, indent=1, sort_keys=True))
        return 0
    for r in rows:
        t = r.get("tokens")
        spent = (f"in={t['input']} cached={t['cached']} out={t['output']}"
                 f" total={t['total']}" if t else r.get("note", "-"))
        if t and r["harness"] == "grok":
            ticks = r.get("cost_usd_ticks")
            spent += (f" cost_usd_ticks={ticks}" if ticks is not None
                      else " cost_usd_ticks=unrecorded")
            if "incomplete" in (r.get("note") or "").lower():
                spent += " (incomplete)"
        print(f"{r['harness']:<7} {str(r['session'])[:12]:<13}"
              f" {str(r.get('model'))[:18]:<19} msgs={r.get('messages')}"
              f"  {spent}")
    return 0


def cmd_report(args) -> int:
    rows = collect(args)
    by = {}
    for r in rows:
        agg = by.setdefault(r["harness"], {
            "sessions": 0, "messages": 0,
            "tokens": {"input": 0, "cached": 0, "output": 0, "total": 0},
            "counted": 0})
        agg["sessions"] += 1
        agg["messages"] += r.get("messages") or 0
        if r.get("tokens"):
            agg["counted"] += 1
            for k in agg["tokens"]:
                agg["tokens"][k] += r["tokens"].get(k, 0)
            if r["harness"] == "grok":
                if r.get("incomplete"):
                    agg["incomplete_sessions"] = agg.get(
                        "incomplete_sessions", 0) + 1
                # Cost aggregates gap-honestly: one counted session
                # with unknown cost makes the total unknown, never a
                # partial sum passed off as complete.
                if r.get("cost_usd_ticks") is None:
                    agg["cost_usd_ticks"] = None
                elif agg.get("cost_usd_ticks", 0) is not None:
                    agg["cost_usd_ticks"] = (agg.get("cost_usd_ticks") or 0
                                             ) + r["cost_usd_ticks"]
    if args.json:
        print(json.dumps({"by_harness": by}, indent=1, sort_keys=True))
        return 0
    for harness in sorted(by):
        agg = by[harness]
        t = agg["tokens"]
        line = (f"{harness:<7} sessions={agg['sessions']}"
                f" messages={agg['messages']}")
        if agg["counted"]:
            line += (f"  in={t['input']} cached={t['cached']}"
                     f" out={t['output']} total={t['total']}")
            if agg["counted"] < agg["sessions"]:
                # The figures cover a subset; saying so is the line's
                # licence to print them at all.
                line += f" counted={agg['counted']}/{agg['sessions']}"
            if agg.get("cost_usd_ticks") is not None:
                line += f" cost_usd_ticks={agg['cost_usd_ticks']}"
            elif harness == "grok":
                line += " cost_usd_ticks=unrecorded"
            if agg.get("incomplete_sessions"):
                # An explicitly incomplete measurement must never read
                # as a verified total.
                line += f" incomplete={agg['incomplete_sessions']}"
        else:
            # The capture rule (loopstrap): an unavailable value is an
            # explicit gap. Zeros here would read as "measured: nothing
            # spent", which is the one thing this line must never say.
            line += f"  tokens=unrecorded ({GROK_GAP})" if harness == "grok"                     else "  tokens=unrecorded"
        print(line)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verb", choices=["sessions", "report"])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repo", default=None,
                        help="only sessions whose cwd is this path")
    home = Path.home()
    parser.add_argument("--claude-dir", default=str(home / ".claude" / "projects"))
    parser.add_argument("--codex-dir", default=str(home / ".codex"))
    parser.add_argument("--grok-dir", default=str(home / ".grok"))
    args = parser.parse_args(argv)
    return cmd_sessions(args) if args.verb == "sessions" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
