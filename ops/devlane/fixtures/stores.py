"""Shared store fixtures in the measured 2026-08-21 harness shapes.

One builder per harness, consolidating the per-test helpers that used
to live in test_breaker.py and test_usage.py. What makes these
trustworthy rather than decorative:

- Time is a variable the caller controls: every builder REQUIRES an
  explicit ``base_timestamp`` (epoch seconds) and never reads a clock,
  so repeated builds with the same arguments are byte-identical.
- The shapes replicate the live stores as measured on 2026-08-21,
  including the awkward parts: Codex SPLITS its metadata (session_meta
  carries id/cwd/base_instructions; model and effort live on a later
  turn_context payload), Grok updates stamp EPOCH INTEGERS while its
  events stamp ISO strings, and Grok's spend lives in updates.jsonl
  turn_completed events (cumulative per run; runs split when totals
  shrink), which ``usage_runs`` emits verbatim.
- Every content channel carries the caller's leak marker — Claude tool
  blocks, Codex base_instructions.text and command payloads, Grok
  session_summary/generated_title/params — so reader tests can prove
  aggregates never leak session content.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def _iso_ms(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def _iso_ns(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond * 1000:09d}Z"


def _write_jsonl(path: Path, entries) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def claude_entry(*, timestamp, cwd, session_id, model, mid, usage, content,
                 git_branch="dev", effort="high"):
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "cwd": cwd,
        "gitBranch": git_branch,
        "effort": effort,
        "isSidechain": False,
        "sessionId": session_id,
        "message": {"id": mid, "model": model, "usage": usage,
                    "content": content},
    }


def build_claude_store(root, slug, *, base_timestamp, cwd, session_id,
                       model, effort, marker, reemit_last=False):
    """Two usage-bearing messages summing to the documented totals
    (input 30, cached 12000, output 500); ``reemit_last`` appends a
    byte-identical copy of the final message — the snapshot-rewrite
    shape whose double-count pulse and breaker must refuse."""
    usage_one = {"input_tokens": 10, "cache_creation_input_tokens": 2_000,
                 "cache_read_input_tokens": 4_000, "output_tokens": 200}
    usage_two = {"input_tokens": 20, "cache_creation_input_tokens": 1_000,
                 "cache_read_input_tokens": 5_000, "output_tokens": 300}
    entries = [
        claude_entry(
            timestamp=_iso_ms(base_timestamp), cwd=cwd,
            session_id=session_id, model=model, effort=effort,
            mid=f"{session_id}-msg-1", usage=usage_one,
            content=[{"type": "tool_use", "name": "Read",
                      "input": {"file_path": marker}}]),
        claude_entry(
            timestamp=_iso_ms(base_timestamp + 300), cwd=cwd,
            session_id=session_id, model=model, effort=effort,
            mid=f"{session_id}-msg-2", usage=usage_two,
            content=[{"type": "tool_use", "name": "Bash",
                      "input": {"command": marker}},
                     {"type": "tool_result", "is_error": False,
                      "content": marker}]),
    ]
    if reemit_last:
        entries.append(entries[-1])
    _write_jsonl(Path(root) / slug / f"{session_id}.jsonl", entries)


def build_codex_store(root, *, base_timestamp, cwd, session_id, model,
                      effort, marker):
    """The measured SPLIT: session_meta holds id, cwd, and the long
    base_instructions prompt (a content channel); model and effort live
    only on turn_context. Token counts are CUMULATIVE — the last one IS
    the spend; summing them is the measured double-count mistake."""
    day = datetime.fromtimestamp(base_timestamp, timezone.utc)
    first_count = {"input_tokens": 200, "cached_input_tokens": 100,
                   "output_tokens": 40, "reasoning_output_tokens": 10,
                   "total_tokens": 240}
    last_count = {"input_tokens": 400, "cached_input_tokens": 300,
                  "output_tokens": 90, "reasoning_output_tokens": 30,
                  "total_tokens": 490}
    entries = [
        {"timestamp": _iso_ms(base_timestamp), "type": "session_meta",
         "payload": {"id": session_id, "cwd": cwd,
                     "base_instructions": {
                         "text": f"You are a coding agent. {marker}"}}},
        {"timestamp": _iso_ms(base_timestamp + 30), "type": "turn_context",
         "payload": {"model": model, "effort": effort, "cwd": cwd}},
        {"timestamp": _iso_ms(base_timestamp + 60), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"total_token_usage": first_count}}},
        {"timestamp": _iso_ms(base_timestamp + 300), "type": "event_msg",
         "payload": {"type": "shell_command", "command": marker}},
        {"timestamp": _iso_ms(base_timestamp + 540), "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"total_token_usage": last_count}}},
    ]
    name = day.strftime("%Y-%m-%dT%H-%M-%S")
    stream = (Path(root) / "sessions" / day.strftime("%Y") / day.strftime("%m")
              / day.strftime("%d") / f"rollout-{name}-{session_id}.jsonl")
    _write_jsonl(stream, entries)


def build_grok_store(root, cwd, *, base_timestamp, session_id, model,
                     marker, usage_runs=None, head_commit=None,
                     git_root_dir=None, grok_home=None):
    """Measured Grok shapes: updates are {method, params, timestamp}
    with EPOCH-INTEGER timestamps; events are {type, ts} (tool events
    add tool_name) with ISO timestamps; summary.json carries content
    channels (session_summary, generated_title) and reasoning_effort
    and never token usage — spend, when present, rides updates.jsonl
    turn_completed events, emitted only from ``usage_runs`` and never
    invented.

    ``head_commit``, ``git_root_dir`` and ``grok_home`` are optional
    stamps the live summary.json carries; callers that need the
    launcher's cross-check against ``snapshot.ref_sha`` pass them.
    """
    session = Path(root) / "sessions" / quote(cwd, safe="") / session_id
    session.mkdir(parents=True, exist_ok=True)
    summary = {
        "info": {"id": session_id, "cwd": cwd},
        "created_at": _iso_ns(base_timestamp),
        "updated_at": _iso_ns(base_timestamp + 1200),
        "num_messages": 3,
        "current_model_id": model,
        "reasoning_effort": "high",
        "session_summary": f"Reviewed the change. {marker}",
        "generated_title": f"Session about {marker}",
    }
    if head_commit is not None:
        summary["head_commit"] = head_commit
    if git_root_dir is not None:
        summary["git_root_dir"] = git_root_dir
    if grok_home is not None:
        summary["grok_home"] = grok_home
    (session / "summary.json").write_text(json.dumps(summary))
    updates = [
        {"method": "session/update",
         "params": {"update": {"kind": "tool", "detail": marker}},
         "timestamp": int(base_timestamp) + 20},
        {"method": "session/update",
         "params": {"update": {"kind": "note", "detail": marker}},
         "timestamp": int(base_timestamp) + 60},
    ]
    # Usage rides updates whose sessionUpdate is turn_completed
    # (measured 2026-08-21 late: cumulative within a run; a run ends
    # when totals shrink). ``usage_runs`` is a list of runs, each a
    # list of cumulative usage dicts emitted VERBATIM — the fixture
    # never invents or normalises currencies.
    stamp = int(base_timestamp) + 90
    for run in usage_runs or ():
        for usage in run:
            updates.append(
                {"method": "session/update",
                 "params": {"update": {"sessionUpdate": "turn_completed",
                                       "usage": usage}},
                 "timestamp": stamp})
            stamp += 30
    _write_jsonl(session / "updates.jsonl", updates)
    _write_jsonl(session / "events.jsonl", [
        {"type": "phase_changed", "ts": _iso_ms(base_timestamp + 10)},
        {"type": "tool_started", "tool_name": "search_code",
         "ts": _iso_ms(base_timestamp + 30)},
        {"type": "tool_completed", "tool_name": "search_code",
         "ts": _iso_ms(base_timestamp + 40)},
        {"type": "permission_requested", "ts": _iso_ms(base_timestamp + 50)},
        {"type": "permission_resolved", "ts": _iso_ms(base_timestamp + 70)},
        {"type": "loop_started", "ts": _iso_ms(base_timestamp + 80)},
        {"type": "phase_changed", "ts": _iso_ms(base_timestamp + 90)},
    ])
